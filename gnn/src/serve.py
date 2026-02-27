import logging
import os
import sys
import json
import asyncio
import torch
import torch.nn as nn
import numpy as np
from aiohttp import web
import aiohttp_cors
from google.cloud import storage
from google.cloud import spanner
from utils.gnn_utils import SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME, INTERVAL_MINUTES, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS
from utils.data import SpannerDataset

# Enhanced logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

from model.stgnn import STGNN
from model.dgat import DGAT
from model.hetgnn import HetGNN

# Configuration
MODELS = {
    "stgnn": {"class": STGNN, "file": "stgnn_model.pth", "scaler": "stgnn_scalers.pkl", "instance": None},
    "dgat":  {"class": DGAT,  "file": "dgat_model.pth",  "scaler": "dgat_scalers.pkl",  "instance": None},
    "hetgnn":{"class": HetGNN,"file": "hetgnn_model.pth","scaler": "hetgnn_scalers.pkl","instance": None}
}
ANOMALY_THRESHOLD = 0.5 

# Shared dependencies
gb = None

# Background task control
background_task_running = False
inference_task = None

def download_blob(bucket_name, source_blob_name, destination_file_name):
    """Downloads a blob from the bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(source_blob_name)
        if blob.exists():
            blob.download_to_filename(destination_file_name)
            logger.info(f"Blob {source_blob_name} downloaded to {destination_file_name}.")
            return True
        else:
            logger.warning(f"Blob {source_blob_name} does not exist in bucket {bucket_name}.")
            return False
    except Exception as e:
        logger.error(f"Failed to download {source_blob_name} from GCS: {e}")
        return False

def load_models():
    global gb, MODELS
    logger.info("Loading GraphBuilder and Models...")
    
    # Download a scaler file first (use DGAT scalers as the shared scaler)
    scaler_file = os.path.join(BASE_DIR, "shared_scalers.pkl")
    
    if GCS_BUCKET_NAME:
        logger.info("Downloading shared scaler file from GCS...")
        # Try to download DGAT scaler as the shared scaler (all models should produce compatible scalers)
        if download_blob(GCS_BUCKET_NAME, f"models/dgat/dgat_scalers.pkl", scaler_file):
            logger.info("Using DGAT scalers as shared scalers")
        elif download_blob(GCS_BUCKET_NAME, f"models/hetgnn/hetgnn_scalers.pkl", scaler_file):
            logger.info("Using HetGNN scalers as shared scalers")
        elif download_blob(GCS_BUCKET_NAME, f"models/stgnn/stgnn_scalers.pkl", scaler_file):
            logger.info("Using STGNN scalers as shared scalers")
        else:
            logger.warning("No scaler files found in GCS, GraphBuilder will not have fitted scalers")
    
    gb = GraphBuilder(scaler_file)
    gb.init_config_encoder()
    
    # Load scalers if available
    if os.path.exists(scaler_file):
        logger.info(f"Loading scalers from {scaler_file}")
        gb.load_scalers()
        logger.info("Scalers loaded successfully")
    else:
        logger.warning(f"Scaler file not found at {scaler_file}, GraphBuilder may not process snapshots correctly")
    
    # Node types match the updated GraphBuilder structure with sub-nodes
    node_types = ["PE Router", "P Router", "CE Router", "Router_Config", "Protocol_State", "Interface", "Interface_Metrics"]
    
    # Edge types include structural edges and sub-node relationships
    edge_types = [
        ("PE Router", "Owns", "Interface"),
        ("P Router", "Owns", "Interface"),
        ("CE Router", "Owns", "Interface"),
        ("Interface", "Connected", "Interface"),
        ("PE Router", "Has_Config", "Router_Config"),
        ("PE Router", "Has_Protocol", "Protocol_State"),
        ("P Router", "Has_Config", "Router_Config"),
        ("P Router", "Has_Protocol", "Protocol_State"),
        ("CE Router", "Has_Config", "Router_Config"),
        ("CE Router", "Has_Protocol", "Protocol_State"),
        ("Interface", "Has_Metrics", "Interface_Metrics")
    ]
    metadata = (node_types, edge_types)
    
    # Input dimensions match the updated GraphBuilder structure
    input_dims = {
        "PE Router": 1,           # Just state
        "P Router": 1,            # Just state
        "CE Router": 1,           # Just state
        "Router_Config": 128,     # Config embedding dimension
        "Protocol_State": 3,      # ospf_neighbors, bgp_peers, mpls_routes
        "Interface": 7,           # state + 6 metrics
        "Interface_Metrics": 12   # 6 metrics + 6 velocities
    }
    
    for name, config in MODELS.items():
        if GCS_BUCKET_NAME:
            logger.info(f"Downloading {name} artifacts from GCS...")
            download_blob(GCS_BUCKET_NAME, f"models/{name}/{config['scaler']}", os.path.join(BASE_DIR, config['scaler']))
            download_blob(GCS_BUCKET_NAME, f"models/{name}/{config['file']}", os.path.join(BASE_DIR, config['file']))

        # Initialize Model Struct
        if name == "stgnn":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS, 'gru', 12)
        elif name == "dgat":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
        elif name == "hetgnn":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS)
        else:
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
            
        instance.set_input_dims(input_dims)
        path = os.path.join(BASE_DIR, config['file'])
        
        if os.path.exists(path):
            try:
                instance.load_state_dict(torch.load(path))
                instance.eval()
                MODELS[name]["instance"] = instance
                logger.info(f"{name.upper()} loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load {name.upper()}: {e}")
        else:
            logger.warning(f"Model file not found at {path}, {name.upper()} skipped.")
            MODELS[name]["instance"] = instance # Instantiate anyway so it doesn't crash, just untrained

async def run_inference():
    global MODELS, gb
    logger.info("="*60)
    logger.info("EXECUTING MULTI-MODEL INFERENCE RUN")
    logger.info("="*60)
        
    if not gb:
        logger.info("GraphBuilder not initialized, loading models...")
        load_models()

    try:
        # Fetch latest Spanner topology
        logger.info(f"Fetching latest snapshot from Spanner (instance={SPANNER_INSTANCE}, db={SPANNER_DATABASE})")
        dataset = SpannerDataset(
            instance_id=SPANNER_INSTANCE, 
            database_id=SPANNER_DATABASE, 
            num_snapshots=1, interval_minutes=INTERVAL_MINUTES
        )
        timestamps = dataset._get_timestamps()
        latest_ts = timestamps[-1]
        logger.info(f"Latest timestamp: {latest_ts}")
        
        data = dataset.fetch_snapshot(latest_ts)
        if not data["nodes"]:
            logger.warning("No data found in Spanner snapshot")
            return {'error': 'No data found in Spanner snapshot'}

        logger.debug(f"Snapshot contains {len(data['nodes'])} nodes, {len(data.get('edges', []))} edges")
        
        hdata, input_dims = gb.process_snapshot(data)
        
        logger.info("Snapshot processed into HeteroData")
        
        # Check if HeteroData has node features
        if not hasattr(hdata, '_node_store_dict') or not any(hasattr(store, 'x') for store in hdata._node_store_dict.values()):
            logger.error("HeteroData object has no node features ('x' attributes)")
            logger.error("This usually means scalers are not fitted/loaded properly")
            logger.error(f"GraphBuilder scaler path: {gb.scaler_path}")
            logger.error(f"Scaler file exists: {os.path.exists(gb.scaler_path)}")
            return {'error': 'No node features in processed snapshot - scalers may not be loaded'}
        
        # Get node types safely
        node_types_in_snapshot = []
        for node_type, store in hdata._node_store_dict.items():
            if hasattr(store, 'x'):
                node_types_in_snapshot.append(node_type)
        
        logger.debug(f"Node types in snapshot: {node_types_in_snapshot}")
        logger.debug(f"Edge types in snapshot: {list(hdata.edge_index_dict.keys()) if hasattr(hdata, 'edge_index_dict') else []}")
        
        for nt in node_types_in_snapshot:
            x = hdata[nt].x
            logger.debug(f"  {nt}: {x.shape[0]} nodes, {x.shape[1]} features")
        
        # We process each model's forward pass sequentially 
        # (could be asyncio.gather in actual remote serving, but PyTorch runs better synchronously on a single container)
        model_results = {}
        criterion = nn.MSELoss(reduction='none')
            
        # 1. DGAT Inference
        logger.info("Running DGAT inference...")
        # Extract edge attributes if available for asymmetry-aware attention
        edge_attr_dict = hdata.edge_attr_dict if hasattr(hdata, 'edge_attr_dict') else None
        with torch.no_grad():
            r_dict, s_dict = MODELS["dgat"]["instance"](hdata.x_dict, hdata.edge_index_dict, edge_attr_dict)
            model_results["dgat"] = (r_dict, s_dict)
            logger.debug(f"DGAT reconstruction node types: {list(r_dict.keys())}")
            logger.debug(f"DGAT embedding node types: {list(s_dict.keys())}")
            if edge_attr_dict:
                logger.debug(f"DGAT used edge attributes for {len(edge_attr_dict)} edge types")
            
        # 2. HetGNN Inference
        logger.info("Running HetGNN inference...")
        with torch.no_grad():
            r_dict, s_dict = MODELS["hetgnn"]["instance"](hdata.x_dict, hdata.edge_index_dict)
            model_results["hetgnn"] = (r_dict, s_dict)
            logger.debug(f"HetGNN reconstruction node types: {list(r_dict.keys())}")
            logger.debug(f"HetGNN embedding node types: {list(s_dict.keys())}")
            
        # 3. STGNN Inference
        logger.info("Running STGNN inference...")
        # STGNN expects temporal sequence tensors `[N, T, F]`. We will fake a simple T=1 sequence here for single-snapshot inference,
        # or in reality this would pull T-1 cached steps.
        temporal_x = {k: v.unsqueeze(1) for k, v in hdata.x_dict.items()}
        logger.debug(f"Created temporal sequences with T=1 for {len(temporal_x)} node types")
        with torch.no_grad():
            # returns recon_dict, out_embeddings, new_hidden_states
            r_dict, s_dict, _ = MODELS["stgnn"]["instance"](temporal_x, hdata.edge_index_dict, None)
            model_results["stgnn"] = (r_dict, s_dict)
            logger.debug(f"STGNN reconstruction node types: {list(r_dict.keys())}")
            logger.debug(f"STGNN embedding node types: {list(s_dict.keys())}")

        # Merge Results mapped by Node ID
        logger.info("Consolidating results from all models...")
        consolidated_nodes = {}
        
        for name, (recon_dict, state_dict) in model_results.items():
            logger.debug(f"Processing {name} results...")
            for node_type, recon_x in recon_dict.items():
                if node_type not in hdata.x_dict: 
                    logger.debug(f"  Skipping {node_type} (not in input data)")
                    continue
                
                # Check STGNN seq target
                target_x = hdata.x_dict[node_type]
                
                loss = criterion(recon_x, target_x).sum(dim=1)
                
                embeddings_list = []
                if name in ["dgat", "hetgnn", "stgnn"] and state_dict and node_type in state_dict:
                    embeddings_list = state_dict[node_type].tolist()
                    
                rev_id_map = {v: k for k, v in gb.global_id_map[node_type].items()}
                
                for i in range(loss.size(0)):
                    if i not in rev_id_map: continue
                    nid = rev_id_map[i]
                    score = loss[i].item()
                    emb = embeddings_list[i] if i < len(embeddings_list) else []
                    
                    if nid not in consolidated_nodes:
                        # Base definition established by the first model that processes this node
                        consolidated_nodes[nid] = {
                            "id": nid,
                            "type": node_type,
                            "stgnn_embedding": [], "stgnn_score": 0.0,
                            "dgat_embedding": [], "dgat_score": 0.0,
                            "hetgnn_embedding": [], "hetgnn_score": 0.0
                        }
                    
                    if name == "stgnn":
                        consolidated_nodes[nid]["stgnn_embedding"] = emb
                        consolidated_nodes[nid]["stgnn_score"] = score
                    elif name == "dgat":
                        consolidated_nodes[nid]["dgat_embedding"] = emb
                        consolidated_nodes[nid]["dgat_score"] = score
                    elif name == "hetgnn":
                        consolidated_nodes[nid]["hetgnn_embedding"] = emb
                        consolidated_nodes[nid]["hetgnn_score"] = score

        logger.info(f"Consolidated {len(consolidated_nodes)} nodes with multi-model embeddings")
        
        # Log anomaly score statistics
        if consolidated_nodes:
            stgnn_scores = [n["stgnn_score"] for n in consolidated_nodes.values() if n["stgnn_score"] > 0]
            dgat_scores = [n["dgat_score"] for n in consolidated_nodes.values() if n["dgat_score"] > 0]
            hetgnn_scores = [n["hetgnn_score"] for n in consolidated_nodes.values() if n["hetgnn_score"] > 0]
            
            if stgnn_scores:
                logger.info(f"STGNN scores - min: {min(stgnn_scores):.4f}, max: {max(stgnn_scores):.4f}, avg: {sum(stgnn_scores)/len(stgnn_scores):.4f}")
            if dgat_scores:
                logger.info(f"DGAT scores - min: {min(dgat_scores):.4f}, max: {max(dgat_scores):.4f}, avg: {sum(dgat_scores)/len(dgat_scores):.4f}")
            if hetgnn_scores:
                logger.info(f"HetGNN scores - min: {min(hetgnn_scores):.4f}, max: {max(hetgnn_scores):.4f}, avg: {sum(hetgnn_scores)/len(hetgnn_scores):.4f}")

        # Create Mutations
        mutations = []
        spanner_timestamp = spanner.COMMIT_TIMESTAMP
        import uuid
        
        for nid, val in consolidated_nodes.items():
            embedding_id = str(uuid.uuid4())
            
            mutations.append((
                embedding_id, nid, val["type"], 
                val["stgnn_embedding"], float(val["stgnn_score"]),
                val["dgat_embedding"], float(val["dgat_score"]),
                val["hetgnn_embedding"], float(val["hetgnn_score"]),
                None, spanner_timestamp
            ))
            
        if mutations:
            logger.info(f"Writing {len(mutations)} multi-model embeddings to Spanner NodeEmbedding table...")
            spanner_client = spanner.Client()
            instance = spanner_client.instance(SPANNER_INSTANCE)
            database = instance.database(SPANNER_DATABASE)
            try:
                with database.batch() as batch:
                    batch.insert(
                        table="NodeEmbedding",
                        columns=("id", "node_id", "node_type", 
                                 "stgnn_embedding", "stgnn_score", "dgat_embedding", "dgat_score", 
                                 "hetgnn_embedding", "hetgnn_score", "anomaly_explanation", "timestamp"),
                        values=mutations
                    )
                logger.info("Successfully wrote embeddings to Spanner")
            except Exception as e:
                logger.error(f"Failed to write embeddings to Spanner: {e}")
        else:
            logger.warning("No mutations to write to Spanner")
                
        # Return generic results array for the UI
        logger.info(f"Inference complete, returning {len(consolidated_nodes)} nodes")
        return {"nodes": list(consolidated_nodes.values()), "global_anomaly": False}
            
    except Exception as e:
        logger.error(f"Inference run failed: {e}", exc_info=True)
        return {'error': str(e)}

async def predict_handler(request):
    logger.info("Received Vertex AI prediction request")
    # Vertex AI Prediction typically sends {"instances": [...]}
    # For now, we ignore the payload and fetch from Spanner directly
    try:
        payload = await request.json()
    except Exception:
        pass
        
    results = await run_inference()
    if 'error' in results:
        status = 500 if results['error'] != 'No data found in Spanner snapshot' else 404
        if results['error'] == 'Model not available': status = 503
        return web.json_response({"predictions": [], "error": results['error']}, status=status)
    return web.json_response({"predictions": results.get("nodes", [])})

async def health_handler(request):
    """Health check for Vertex AI"""
    return web.json_response({"status": "healthy"}, status=200)

async def background_inference_loop():
    """
    Background task that runs inference every 60 seconds.
    Writes embeddings to Spanner NodeEmbedding table automatically.
    """
    global background_task_running
    background_task_running = True
    
    logger.info("🚀 Background inference task started (60-second interval)")
    
    while background_task_running:
        try:
            logger.info("⏰ Background inference triggered")
            await run_inference()
            logger.info(f"✅ Background inference complete, sleeping 60 seconds...")
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"❌ Background inference error: {e}", exc_info=True)
            logger.info("Retrying in 60 seconds...")
            await asyncio.sleep(60)  # Still sleep on error to avoid tight loop
    
    logger.info("Background inference task stopped")

async def start_background_tasks(app):
    """Called when aiohttp app starts - launches background inference loop"""
    global inference_task
    logger.info("Starting background tasks...")
    inference_task = asyncio.create_task(background_inference_loop())
    logger.info("Background inference task launched")

async def cleanup_background_tasks(app):
    """Called when aiohttp app shuts down - gracefully stops background task"""
    global background_task_running, inference_task
    logger.info("Shutting down background tasks...")
    background_task_running = False
    if inference_task:
        await inference_task
    logger.info("Background tasks stopped")

# Initialize aiohttp app and CORS
app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*"
    )
})

# Wire background task lifecycle to app startup/cleanup
app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)

if __name__ == "__main__":
    # Load model on start
    load_models()
    
    # Add Vertex AI routes
    predict_route_path = os.environ.get('AIP_PREDICT_ROUTE', '/predict')
    health_route_path = os.environ.get('AIP_HEALTH_ROUTE', '/health')
    
    predict_route = app.router.add_post(predict_route_path, predict_handler)
    health_route = app.router.add_get(health_route_path, health_handler)
    
    # Add CORS to API routes
    cors.add(predict_route)
    cors.add(health_route)

    logger.info("Serving GNN on Vertex AI...")
    port = int(os.environ.get('AIP_HTTP_PORT', 8080))
    web.run_app(app, port=port)
