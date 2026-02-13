
import os
import sys
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib

# Local imports
from data import SpannerDataset
from gnn_utils import THGAT, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS

# Configuration
INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
DATABASE_ID = os.getenv("SPANNER_DATABASE", "networktopology-db")
GCS_BUCKET = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts") # Update with actual default if known

MODEL_SAVE_PATH = "model.pth"
SCALER_PATH = "scalers.pkl"
EPOCHS = 50
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 50
INTERVAL_MINUTES = 5

def upload_blob(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name)
        print(f"File {source_file_name} uploaded to {destination_blob_name}.")
    except Exception as e:
        print(f"Failed to upload {source_file_name} to GCS: {e}")

def train_job():
    print("="*60)
    print(f"THGAT TRAINING SERVICE")
    print(f"Instance: {INSTANCE_ID}, Database: {DATABASE_ID}")
    print("="*60)

    print("Initializing GraphBuilder...")
    gb = GraphBuilder(SCALER_PATH)
    gb.init_netbert()

    print(f"Fetching {TRAINING_SNAPSHOTS} snapshots from Spanner...")
    dataset = SpannerDataset(INSTANCE_ID, DATABASE_ID, num_snapshots=TRAINING_SNAPSHOTS, interval_minutes=INTERVAL_MINUTES)
    
    timestamps = dataset._get_timestamps()
    snapshot_objects = []
    
    for ts in tqdm(timestamps, desc="Fetching Snapshots"):
        try:
            snapshot = dataset.fetch_snapshot(ts)
            if snapshot["nodes"]:
                snapshot_objects.append(snapshot)
            else:
                print(f"Warning: Empty snapshot at {ts}")
        except Exception as e:
            print(f"Error fetching snapshot at {ts}: {e}")
            
    if not snapshot_objects:
        print("Error: No data found. Exiting.")
        return

    print("Fitting scalers...")
    gb.fit_scalers(snapshot_objects)
    gb.save_scalers()
    print(f"Scalers saved locally to {SCALER_PATH}")

    print("Processing snapshots into HeteroData...")
    snapshots = []
    input_dims = None
    
    for data in tqdm(snapshot_objects, desc="Processing Graphs"):
        hdata, dims = gb.process_snapshot(data)
        snapshots.append(hdata)
        if input_dims is None:
            input_dims = dims
            
    # Construct metadata (Node Types, Edge Types) from first snapshot
    # Note: SpannerDataset should ensure consistent types or we handle dynamic logic
    # Here we assume schema consistency.
    node_types = list(input_dims.keys())
    # We need edge types from the actual data or schema definition.
    # The first snapshot might not have all edge types if empty.
    # We should iterate to find all present edge types or hardcode expected schema.
    # Let's collect all edge types seen
    all_edge_types = set()
    for s in snapshots:
        for et in s.edge_index_dict.keys():
            all_edge_types.add(et)
    
    edge_types = list(all_edge_types)
    metadata = (node_types, edge_types)
    
    print(f"Model Metadata: {metadata}")
    
    print("Initializing THGAT Model...")
    model = THGAT(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
    model.set_input_dims(input_dims)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    criterion = nn.MSELoss()
    
    print("Starting Training...")
    model.train()
    
    for epoch in range(EPOCHS):
        total_loss = 0
        hidden_state = None 
        
        for snapshot in snapshots:
            optimizer.zero_grad()
            
            # Detach hidden state to truncate BPTT if needed (or keep strictly sequential)
            # Typically for long sequences we detach to avoid OOM or huge graphs
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
            print(f"Epoch {epoch+1}/{EPOCHS}, Loss: {total_loss:.4f}")
            
    print(f"Saving model locally to {MODEL_SAVE_PATH}...")
    torch.save(model.state_dict(), MODEL_SAVE_PATH)
    
    print("Uploading artifacts to GCS...")
    if GCS_BUCKET:
        upload_blob(GCS_BUCKET, MODEL_SAVE_PATH, f"models/thgat/{MODEL_SAVE_PATH}")
        upload_blob(GCS_BUCKET, SCALER_PATH, f"models/thgat/{SCALER_PATH}")
    else:
        print("GCS_BUCKET_NAME not set. Skipping upload.")
        
    print("Done.")

if __name__ == "__main__":
    train_job()
