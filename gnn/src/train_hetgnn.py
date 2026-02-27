import os
import sys
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib
import logging
from model.hetgnn import HetGNN
from utils.data import SpannerDataset
from utils.gnn_utils import SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME, INTERVAL_MINUTES, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
MODEL_SAVE_PATH = "hetgnn_model.pth"
SCALER_PATH = "hetgnn_scalers.pkl"
EPOCHS = 50
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 20
VALIDATION_SPLIT = 0.2  # 20% for validation
EARLY_STOPPING_PATIENCE = 10  # Stop if no improvement for 10 epochs
MIN_DELTA = 0.001  # Minimum change to qualify as an improvement

# Multi-task objective weights
ALPHA = 0.4  # Weight for Config Schema Loss
BETA = 0.4   # Weight for Protocol State Schema Loss
GAMMA = 0.2  # Weight for Interface Metrics Loss

def upload_blob(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name)
        logger.info(f"File {source_file_name} uploaded to {destination_blob_name}.")
    except Exception as e:
        logger.error(f"Failed to upload {source_file_name} to GCS: {e}")

def run_training_pipeline():
    logger.info("="*60)
    logger.info(f"HETGNN TRAINING SERVICE STARTED")
    logger.info(f"Instance: {SPANNER_INSTANCE}, Database: {SPANNER_DATABASE}")
    logger.info("="*60)

    try:
        gb = GraphBuilder(SCALER_PATH)
        gb.init_config_encoder()

        dataset = SpannerDataset(SPANNER_INSTANCE, SPANNER_DATABASE, num_snapshots=TRAINING_SNAPSHOTS, interval_minutes=INTERVAL_MINUTES)
        
        timestamps = dataset._get_timestamps()
        snapshot_objects = []
        
        for ts in tqdm(timestamps, desc="Fetching Snapshots"):
            try:
                snapshot = dataset.fetch_snapshot(ts)
                if snapshot["nodes"]:
                    snapshot_objects.append(snapshot)
            except Exception as e:
                logger.error(f"Error fetching snapshot at {ts}: {e}")
                
        if not snapshot_objects:
            logger.error("Error: No data found. Exiting.")
            return

        logger.info("Fitting scalers...")
        gb.fit_scalers(snapshot_objects)
        gb.save_scalers()

        logger.info("Processing snapshots into HeteroData...")
        snapshots = []
        input_dims = None
        
        for idx, data in enumerate(snapshot_objects):
            logger.debug(f"Processing snapshot {idx+1}/{len(snapshot_objects)}")
            hdata, dims = gb.process_snapshot(data)
            snapshots.append(hdata)
            if input_dims is None:
                input_dims = dims
                logger.info(f"Input dimensions: {input_dims}")

        node_types = list(input_dims.keys())
        all_edge_types = set()
        for s in snapshots:
            for et in s.edge_index_dict.keys():
                all_edge_types.add(et)
        
        metadata = (node_types, list(all_edge_types))
        
        logger.info(f"Node types ({len(node_types)}): {node_types}")
        logger.info(f"Edge types ({len(all_edge_types)}): {list(all_edge_types)}")
        
        # Log snapshot statistics
        if snapshots:
            sample = snapshots[0]
            logger.info("Sample snapshot statistics:")
            for nt in node_types:
                if nt in sample.x_dict:
                    logger.info(f"  {nt}: {sample.x_dict[nt].shape[0]} nodes, {sample.x_dict[nt].shape[1]} features")
            for et in all_edge_types:
                if et in sample.edge_index_dict:
                    logger.info(f"  {et}: {sample.edge_index_dict[et].shape[1]} edges")
        
        # Split data into train and validation sets
        split_idx = int(len(snapshots) * (1 - VALIDATION_SPLIT))
        train_snapshots = snapshots[:split_idx]
        val_snapshots = snapshots[split_idx:]
        
        logger.info(f"Dataset split: {len(train_snapshots)} training, {len(val_snapshots)} validation snapshots")
        
        logger.info(f"Creating HetGNN model with hidden_channels={HIDDEN_CHANNELS}, num_layers={NUM_LAYERS}")
        model = HetGNN(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS)
        model.set_input_dims(input_dims)
        
        optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
        # Add learning rate scheduler to reduce LR when validation loss plateaus
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', factor=0.5, patience=5, min_lr=1e-6
        )
        criterion = nn.MSELoss(reduction='sum')
        
        logger.info("Model created (parameters will be initialized on first forward pass)")
        logger.info(f"Learning rate scheduler: ReduceLROnPlateau (factor=0.5, patience=5)")
        
        logger.info(f"Starting training for up to {EPOCHS} epochs with early stopping (patience={EARLY_STOPPING_PATIENCE})")
        logger.info(f"Multi-task weights: α={ALPHA} (config), β={BETA} (protocol), γ={GAMMA} (metrics)")
        
        # Early stopping variables
        best_val_loss = float('inf')
        epochs_without_improvement = 0
        best_model_state = None
        
        for epoch in range(EPOCHS):
            # ============ TRAINING PHASE ============
            model.train()
            train_loss_tensors = []
            train_config_tensors = []
            train_protocol_tensors = []
            train_metrics_tensors = []
            
            for snap_idx, snapshot in enumerate(train_snapshots):
                optimizer.zero_grad()
                
                # Log first forward pass details
                if epoch == 0 and snap_idx == 0:
                    logger.debug(f"First forward pass - input node types: {list(snapshot.x_dict.keys())}")
                    logger.debug(f"First forward pass - edge types: {list(snapshot.edge_index_dict.keys())}")
                
                recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict)
                
                # Log reconstruction output
                if epoch == 0 and snap_idx == 0:
                    logger.debug(f"Reconstruction output node types: {list(recon_dict.keys())}")
                    logger.debug(f"Embedding output node types: {list(embeddings.keys())}")
                
                loss_config = 0
                loss_protocol = 0
                loss_metrics = 0
                
                # Compute multi-task objective loss
                for node_type, recon_x in recon_dict.items():
                    if node_type in snapshot.x_dict:
                        node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                        
                        # Segregate loss accumulation by node type (config, protocol, metrics branches)
                        # Match actual node type names from GraphBuilder
                        if "Router_Config" in node_type:
                            loss_config += node_loss
                        elif "Protocol_State" in node_type:
                            loss_protocol += node_loss
                        else:
                            # Interface, Interface_Metrics, and Router nodes go to metrics
                            loss_metrics += node_loss
                            
                total_weighted_loss = (ALPHA * loss_config) + (BETA * loss_protocol) + (GAMMA * loss_metrics)
                
                total_weighted_loss.backward()
                optimizer.step()
                
                # Accumulate losses as tensors (detached from computation graph)
                train_loss_tensors.append(total_weighted_loss.detach())
                if isinstance(loss_config, torch.Tensor) and loss_config.numel() > 0:
                    train_config_tensors.append(loss_config.detach())
                if isinstance(loss_protocol, torch.Tensor) and loss_protocol.numel() > 0:
                    train_protocol_tensors.append(loss_protocol.detach())
                if isinstance(loss_metrics, torch.Tensor) and loss_metrics.numel() > 0:
                    train_metrics_tensors.append(loss_metrics.detach())
            
            # Calculate training losses
            train_loss = torch.stack(train_loss_tensors).sum().item()
            train_loss_config = torch.stack(train_config_tensors).sum().item() if train_config_tensors else 0.0
            train_loss_protocol = torch.stack(train_protocol_tensors).sum().item() if train_protocol_tensors else 0.0
            train_loss_metrics = torch.stack(train_metrics_tensors).sum().item() if train_metrics_tensors else 0.0
            
            # ============ VALIDATION PHASE ============
            model.eval()
            val_loss_tensors = []
            val_config_tensors = []
            val_protocol_tensors = []
            val_metrics_tensors = []
            
            with torch.no_grad():
                for snapshot in val_snapshots:
                    recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict)
                    
                    loss_config = 0
                    loss_protocol = 0
                    loss_metrics = 0
                    
                    for node_type, recon_x in recon_dict.items():
                        if node_type in snapshot.x_dict:
                            node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                            
                            if "Router_Config" in node_type:
                                loss_config += node_loss
                            elif "Protocol_State" in node_type:
                                loss_protocol += node_loss
                            else:
                                loss_metrics += node_loss
                    
                    total_weighted_loss = (ALPHA * loss_config) + (BETA * loss_protocol) + (GAMMA * loss_metrics)
                    
                    val_loss_tensors.append(total_weighted_loss.detach())
                    if isinstance(loss_config, torch.Tensor) and loss_config.numel() > 0:
                        val_config_tensors.append(loss_config.detach())
                    if isinstance(loss_protocol, torch.Tensor) and loss_protocol.numel() > 0:
                        val_protocol_tensors.append(loss_protocol.detach())
                    if isinstance(loss_metrics, torch.Tensor) and loss_metrics.numel() > 0:
                        val_metrics_tensors.append(loss_metrics.detach())
            
            val_loss = torch.stack(val_loss_tensors).sum().item()
            val_loss_config = torch.stack(val_config_tensors).sum().item() if val_config_tensors else 0.0
            val_loss_protocol = torch.stack(val_protocol_tensors).sum().item() if val_protocol_tensors else 0.0
            val_loss_metrics = torch.stack(val_metrics_tensors).sum().item() if val_metrics_tensors else 0.0
            
            # Step the learning rate scheduler
            scheduler.step(val_loss)
            current_lr = optimizer.param_groups[0]['lr']
            
            # ============ LOGGING ============
            if (epoch + 1) % 5 == 0:
                logger.info(f"Epoch {epoch+1}/{EPOCHS}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}, LR: {current_lr:.6f}")
                logger.info(f"  Train - Config: {train_loss_config:.4f}, Protocol: {train_loss_protocol:.4f}, Metrics: {train_loss_metrics:.4f}")
                logger.info(f"  Val   - Config: {val_loss_config:.4f}, Protocol: {val_loss_protocol:.4f}, Metrics: {val_loss_metrics:.4f}")
            else:
                logger.debug(f"Epoch {epoch+1}/{EPOCHS}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}")
            
            # ============ EARLY STOPPING CHECK ============
            if val_loss < best_val_loss - MIN_DELTA:
                best_val_loss = val_loss
                epochs_without_improvement = 0
                best_model_state = model.state_dict().copy()
                logger.info(f"  ✓ New best validation loss: {best_val_loss:.4f}")
            else:
                epochs_without_improvement += 1
                if epochs_without_improvement >= EARLY_STOPPING_PATIENCE:
                    logger.info(f"Early stopping triggered after {epoch+1} epochs (no improvement for {EARLY_STOPPING_PATIENCE} epochs)")
                    logger.info(f"Best validation loss: {best_val_loss:.4f}")
                    # Restore best model
                    if best_model_state is not None:
                        model.load_state_dict(best_model_state)
                        logger.info("Restored best model weights")
                    break
            
        logger.info(f"Saving model locally to {MODEL_SAVE_PATH}...")
        torch.save(model.state_dict(), MODEL_SAVE_PATH)
        
        if GCS_BUCKET_NAME:
            upload_blob(GCS_BUCKET_NAME, MODEL_SAVE_PATH, f"models/hetgnn/{MODEL_SAVE_PATH}")
            upload_blob(GCS_BUCKET_NAME, SCALER_PATH, f"models/hetgnn/{SCALER_PATH}")
            
    except Exception as e:
        logger.error(f"Training pipeline failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_training_pipeline()
