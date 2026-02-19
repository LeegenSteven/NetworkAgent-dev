import logging
import os
import json
import torch
import torch.nn as nn
import numpy as np
from aiohttp import web
import aiohttp_cors
from google.cloud import storage
from google.cloud import spanner
import datetime
from utils.gnn_utils import SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME, INTERVAL_MINUTES, THGAT, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS, explain_node_anomaly
from utils.data import SpannerDataset

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# Configuration
MODEL_PATH = os.path.join(BASE_DIR, "model.pth")
SCALER_PATH = os.path.join(BASE_DIR, "scalers.pkl")
ANOMALY_THRESHOLD = 0.5 

# Initialize aiohttp application with no middleware
app = web.Application()

# Setup CORS for aiohttp routes
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

# Global variables for model and graph builder
gb = None
model = None
hidden_state = None  # For stateful inference if needed, though REST assumes stateless usually. 
# However, THGAT is temporal. For a simple "snapshot inference", we might ignore prev state or keep it in memory.
# Given the user asked for "takes a snapshot... returns embeddings", strict stateless might be expected, 
# but statefulness improves accuracy. We'll keep it simple: stateless for now (reset hidden state) or 
# ideally we'd cache it per graph ID? For this task, we will reset it or just pass None.
# "runs through the trained GNN" implies we might want to capture context if it's a stream, 
# but a POST endpoint suggests single-shot. 
# We'll use None for hidden_state to treat it as a fresh sequence start or single step.

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

def load_model():
    global gb, model
    logger.info("Loading GraphBuilder and Model...")
    
    if GCS_BUCKET_NAME:
        logger.info(f"Attempting to download artifacts from GCS bucket: {GCS_BUCKET_NAME}")
        download_blob(GCS_BUCKET_NAME, "models/thgat/scalers.pkl", SCALER_PATH)
        download_blob(GCS_BUCKET_NAME, "models/thgat/model.pth", MODEL_PATH)

    gb = GraphBuilder(SCALER_PATH)
    gb.init_netbert()
    
    if not gb.load_scalers():
        logger.warning(f"Scalers not found at {SCALER_PATH}. Inference might be inaccurate.")

    # We need metadata to initialize the model. 
    # If we don't have it, we can't load the model until we see data or save metadata.
    # Matching gnn_utils.py training master types.
    node_types = ["PE Router", "P Router", "CE Router", "Interface"]
    edge_types = [
        ("PE Router", "Owns", "Interface"),
        ("P Router", "Owns", "Interface"),
        ("CE Router", "Owns", "Interface"),
        ("Interface", "Connected", "Interface")
    ]
    metadata = (node_types, edge_types)
    
    model = THGAT(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
    
    # We need input dims to set projection layers (Linear)
    # This is tricky without data. We can try to load state dict and infer, 
    # or wait for first request.
    # But `load_state_dict` requires the model structure to match.
    # THGAT.set_input_dims creates the layers. We MUST know input dims before loading state dict.
    # Code in `gnn_utils.py` shows `set_input_dims` creates `lin_dict` and `decoder_dict`.
    # These are parameters.
    
    # Let's assume standard input dims or try to load them? 
    # The `thgat-network-demo` reconstructs them from `input_dims`.
    # We might need to save input_dims with the model or scalers.
    # For now, let's lazy-load on first request or try to guess.
    
    if os.path.exists(MODEL_PATH):
        try:
            # Hardcoded standard dims based on gnn_utils.py
            # Router: 1 + 128 = 129
            # Interface: 4
            input_dims = {
                "PE Router": 129, 
                "P Router": 129, 
                "CE Router": 129,
                "Interface": 4
            }
            
            model.set_input_dims(input_dims)
            model.load_state_dict(torch.load(MODEL_PATH))
            model.eval()
            logger.info("Model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            model = None
    else:
        logger.warning(f"Model file not found at {MODEL_PATH}")


async def run_inference():
    global model, gb
    logger.info("Executing inference run")
        
    if not model or not gb:
        # Try to reload
        load_model()
        if not model:
            logger.error("Model not available for inference")
            return {'error': 'Model not available'}

    try:
        # Fetch the latest snapshot from Spanner
        dataset = SpannerDataset(
            instance_id=SPANNER_INSTANCE, 
            database_id=SPANNER_DATABASE, 
            num_snapshots=1, interval_minutes=INTERVAL_MINUTES # We only need the latest one for inference
        )
        
        # _get_timestamps returns a list ending at now()
        timestamps = dataset._get_timestamps()
        latest_ts = timestamps[-1]
        
        logger.info(f"Fetching snapshot for timestamp: {latest_ts}")
        data = dataset.fetch_snapshot(latest_ts)
        
        if not data["nodes"]:
            logger.warning("No data found in Spanner snapshot")
            return {'error': 'No data found in Spanner snapshot'}

        # Process snapshot
        hdata, input_dims = gb.process_snapshot(data)
        
        # Ensure model has input dims if not set (first run case)
        if len(model.lin_dict) == 0:
             logger.info("Setting input dims from request data")
             model.set_input_dims(input_dims)
        
        with torch.no_grad():
            # Stateless run for single snapshot
            # Capture state_dict (hidden state) which represents the node embeddings
            recon_dict, state_dict = model(hdata.x_dict, hdata.edge_index_dict, None)
            
            criterion = nn.MSELoss(reduction='none')
            results = {
                "nodes": [],
                "global_anomaly": False,
                "average_score": 0.0
            }
            
            total_loss = 0
            node_count = 0
            
            for node_type, recon_x in recon_dict.items():
                if node_type in hdata.x_dict:
                    # Using Sum Squared Error (SSE) instead of Mean Squared Error
                    # This makes the score proportional to the number of mismatched features
                    # which is better for sparse/binary feature vectors.
                    loss = criterion(recon_x, hdata.x_dict[node_type]).sum(dim=1)
                    
                    # Get embeddings from state_dict
                    # state_dict[node_type] is (num_layers, batch, hidden_channels) -> (1, N, 64)
                    # We want (N, 64)
                    if state_dict and node_type in state_dict:
                        # Squeeze the first dimension (layers)
                        embeddings = state_dict[node_type].squeeze(0).tolist()
                    else:
                         embeddings = []

                    # Map back to IDs
                    rev_id_map = {v: k for k, v in gb.global_id_map[node_type].items()}
                    
                    for i in range(loss.size(0)):
                        if i not in rev_id_map: continue
                        nid = rev_id_map[i]
                        score = loss[i].item()
                        is_anomaly = score > ANOMALY_THRESHOLD
                        
                        # Always generate explanation for better insights
                        explanation = explain_node_anomaly(node_type, hdata.x_dict[node_type][i], recon_x[i])
                        
                        node_result = {
                            "id": nid,
                            "type": node_type,
                            "score": score,
                            "is_anomaly": is_anomaly,
                            "explanation": explanation
                        }
                        
                        if i < len(embeddings):
                            node_result["embedding"] = embeddings[i]
                            
                        results["nodes"].append(node_result)
                        
                        total_loss += score
                        node_count += 1
                        
            # --- Write embeddings to Spanner ---
            mutations = []
            spanner_timestamp = spanner.COMMIT_TIMESTAMP
            import uuid
            
            # Use results["nodes"] which already contains all computed info
            for node in results["nodes"]:
                if "embedding" not in node:
                    continue
                    
                embedding_id = str(uuid.uuid4())
                nid = node["id"]
                node_type = node["type"]
                emb = node["embedding"]
                score = node["score"]
                # Spanner JSON column expects a JSON-encoded string or None
                explanation = node.get("explanation")
                
                # Convert dict to JSON string for Spanner's JSON column
                if explanation is not None:
                    explanation = json.dumps(explanation)
                
                mutations.append(
                    (embedding_id, nid, node_type, emb, float(score), explanation, spanner_timestamp)
                )
            
            if mutations:
                logger.info(f"Writing {len(mutations)} embeddings to Spanner from inference...")
                spanner_client = spanner.Client()
                instance = spanner_client.instance(SPANNER_INSTANCE)
                database = instance.database(SPANNER_DATABASE)
                
                # Check if the table exists, assuming it might not yet or using a try-except
                try:
                    with database.batch() as batch:
                        batch.insert(
                            table="NodeEmbedding",
                            columns=("id", "node_id", "node_type", "embedding", "anomaly_score", "anomaly_explanation", "timestamp"),
                            values=mutations
                        )
                    logger.info("Successfully wrote embeddings to Spanner.")
                except Exception as e:
                    logger.error(f"Failed to write embeddings to Spanner: {e}")
            
            if node_count > 0:
                results["average_score"] = total_loss / node_count
                
            results["global_anomaly"] = results["average_score"] > ANOMALY_THRESHOLD
            
            return results
            
    except Exception as e:
        logger.error(f"Inference run failed: {e}", exc_info=True)
        return {'error': str(e)}

async def inference_handler(request):
    logger.info("Received REST inference request")
    results = await run_inference()
    if 'error' in results:
        status = 500 if results['error'] != 'No data found in Spanner snapshot' else 404
        if results['error'] == 'Model not available': status = 503
        return web.json_response(results, status=status)
    return web.json_response(results)

async def inference_loop(app):
    import asyncio
    global background_task_running
    logger.info("Starting background inference loop (every 60s)")
    background_task_running = True
    try:
        while True:
            try:
                await run_inference()
            except asyncio.CancelledError:
                logger.info("Background inference loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background inference loop: {e}", exc_info=True)
            await asyncio.sleep(60)
    finally:
        background_task_running = False
        logger.info("Background inference loop stopped")

async def start_background_tasks(app):
    import asyncio
    global inference_task, background_task_running
    
    if inference_task and not inference_task.done():
        logger.warning("Background task already running")
        return False
    
    inference_task = asyncio.create_task(inference_loop(app))
    logger.info("Background inference task started")
    return True

async def cleanup_background_tasks(app):
    global inference_task, background_task_running
    import asyncio
    
    if inference_task and not inference_task.done():
        inference_task.cancel()
        await asyncio.gather(inference_task, return_exceptions=True)
        background_task_running = False
        logger.info("Background tasks cleaned up")

async def start_handler(request):
    """REST endpoint to start the background inference loop"""
    global inference_task, background_task_running
    
    if inference_task and not inference_task.done():
        return web.json_response({
            "status": "already_running",
            "message": "Background inference task is already running"
        }, status=200)
    
    success = await start_background_tasks(request.app)
    
    if success:
        return web.json_response({
            "status": "started",
            "message": "Background inference task started successfully"
        }, status=200)
    else:
        return web.json_response({
            "status": "error",
            "message": "Failed to start background inference task"
        }, status=500)

async def stop_handler(request):
    """REST endpoint to stop the background inference loop"""
    global inference_task, background_task_running
    
    if not inference_task or inference_task.done():
        return web.json_response({
            "status": "not_running",
            "message": "Background inference task is not running"
        }, status=200)
    
    try:
        inference_task.cancel()
        import asyncio
        await asyncio.gather(inference_task, return_exceptions=True)
        background_task_running = False
        
        return web.json_response({
            "status": "stopped",
            "message": "Background inference task stopped successfully"
        }, status=200)
    except Exception as e:
        logger.error(f"Error stopping background task: {e}", exc_info=True)
        return web.json_response({
            "status": "error",
            "message": f"Error stopping background task: {str(e)}"
        }, status=500)

async def status_handler(request):
    """REST endpoint to check the status of the background inference loop"""
    global inference_task, background_task_running
    
    is_running = inference_task is not None and not inference_task.done()
    
    return web.json_response({
        "status": "running" if is_running else "stopped",
        "task_exists": inference_task is not None,
        "task_done": inference_task.done() if inference_task else True,
        "background_flag": background_task_running
    }, status=200)



if __name__ == "__main__":
    # Load model on start
    load_model()
    
    # Optional: Auto-start background tasks (set AUTO_START=true to enable)
    auto_start = os.environ.get('AUTO_START', 'false').lower() == 'true'
    if auto_start:
        app.on_startup.append(start_background_tasks)
        logger.info("Background tasks will auto-start on service startup")
    else:
        logger.info("Background tasks will NOT auto-start. Use POST /start to begin.")
    
    app.on_cleanup.append(cleanup_background_tasks)
    
    # Add routes
    inference_route = app.router.add_post('/inference', inference_handler)
    start_route = app.router.add_get('/start', start_handler)
    stop_route = app.router.add_get('/stop', stop_handler)
    status_route = app.router.add_get('/status', status_handler)
    
    # Add CORS to API routes
    cors.add(inference_route)
    cors.add(start_route)
    cors.add(stop_route)
    cors.add(status_route)

    logger.info("serving gnn...")
    port = int(os.environ.get('PORT', 8082))
    web.run_app(app, port=port)
