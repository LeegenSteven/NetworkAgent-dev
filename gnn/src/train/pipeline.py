
import os
import sys
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib
import logging
from utils.data import SpannerDataset
from utils.gnn_utils import THGAT, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS

logger = logging.getLogger(__name__)

# Configuration
INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
DATABASE_ID = os.getenv("SPANNER_DATABASE", "networktopology-db")
GCS_BUCKET = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts")

MODEL_SAVE_PATH = "model.pth"
SCALER_PATH = "scalers.pkl"
EPOCHS = 50
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 100
INTERVAL_MINUTES = 1

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
    logger.info(f"THGAT TRAINING SERVICE STARTED")
    logger.info(f"Instance: {INSTANCE_ID}, Database: {DATABASE_ID}")
    logger.info("="*60)

    try:
        logger.info("Initializing GraphBuilder...")
        gb = GraphBuilder(SCALER_PATH)
        gb.init_netbert()

        logger.info(f"Fetching {TRAINING_SNAPSHOTS} snapshots from Spanner...")
        dataset = SpannerDataset(INSTANCE_ID, DATABASE_ID, num_snapshots=TRAINING_SNAPSHOTS, interval_minutes=INTERVAL_MINUTES)
        
        timestamps = dataset._get_timestamps()
        snapshot_objects = []
        
        for ts in tqdm(timestamps, desc="Fetching Snapshots"):
            try:
                snapshot = dataset.fetch_snapshot(ts)
                logger.info(snapshot)

                if snapshot["nodes"]:
                    snapshot_objects.append(snapshot)
                else:
                    logger.warning(f"Warning: Empty snapshot at {ts}")
            except Exception as e:
                logger.error(f"Error fetching snapshot at {ts}: {e}")
                
        if not snapshot_objects:
            logger.error("Error: No data found. Exiting.")
            return

        logger.info("Fitting scalers...")
        gb.fit_scalers(snapshot_objects)
        gb.save_scalers()
        logger.info(f"Scalers saved locally to {SCALER_PATH}")

        logger.info("Processing snapshots into HeteroData...")
        snapshots = []
        input_dims = None
        
        for data in tqdm(snapshot_objects, desc="Processing Graphs"):
            hdata, dims = gb.process_snapshot(data)
            snapshots.append(hdata)
            if input_dims is None:
                input_dims = dims
                
        # Derive metadata
        node_types = list(input_dims.keys())
        all_edge_types = set()
        for s in snapshots:
            for et in s.edge_index_dict.keys():
                all_edge_types.add(et)
        
        edge_types = list(all_edge_types)
        metadata = (node_types, edge_types)
        
        logger.info(f"Model Metadata: {metadata}")
        
        logger.info("Initializing THGAT Model...")
        model = THGAT(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
        model.set_input_dims(input_dims)
        
        optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
        criterion = nn.MSELoss()
        
        logger.info("Starting Training...")
        model.train()
        
        for epoch in range(EPOCHS):
            logger.info(f"epoch {epoch}")
            total_loss = 0
            hidden_state = None 
            
            for snapshot in snapshots:
                optimizer.zero_grad()
                if hidden_state:
                    hidden_state = {k: v.detach() for k, v in hidden_state.items()}
                    
                recon_dict, hidden_state = model(snapshot.x_dict, snapshot.edge_index_dict, hidden_state)
                
                loss = 0
                for node_type, recon_x in recon_dict.items():
                    if node_type in snapshot.x_dict:
                        loss += criterion(recon_x, snapshot.x_dict[node_type])
                
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
                
            if (epoch + 1) % 5 == 0:
                logger.info(f"Epoch {epoch+1}/{EPOCHS}, Loss: {total_loss:.4f}")
            
        logger.info(f"Saving model locally to {MODEL_SAVE_PATH}...")
        torch.save(model.state_dict(), MODEL_SAVE_PATH)
        
        logger.info("Uploading artifacts to GCS...")
        if GCS_BUCKET:
            upload_blob(GCS_BUCKET, MODEL_SAVE_PATH, f"models/thgat/{MODEL_SAVE_PATH}")
            upload_blob(GCS_BUCKET, SCALER_PATH, f"models/thgat/{SCALER_PATH}")
        else:
            logger.info("GCS_BUCKET_NAME not set. Skipping upload.")
            
        logger.info("Training pipeline completed successfully.")
        
    except Exception as e:
        logger.error(f"Training pipeline failed: {e}")
        import traceback
        traceback.print_exc()

