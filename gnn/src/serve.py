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
from utils.gnn_utils import THGAT, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS, explain_node_anomaly
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
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts")
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "networktopology-db")

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
            # Router: 1 + 768 = 769
            # Interface: 4
            input_dims = {
                "PE Router": 769, 
                "P Router": 769, 
                "CE Router": 769,
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
            num_snapshots=1 # We only need the latest one for inference
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
                    loss = criterion(recon_x, hdata.x_dict[node_type]).mean(dim=1)
                    
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
                        
                        explanation = None
                        if is_anomaly:
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
                # Spanner JSON column expects a dict/list or None.
                explanation = node.get("explanation") 
                
                # Explanation might be a dict, ensure it's JSON serializable if wrapper objects exist
                # But typically it's just a dict from explain_node_anomaly
                
                mutations.append(
                    (embedding_id, nid, node_type, emb, score, explanation, spanner_timestamp)
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
                            columns=("id", "node_id", "node_type", "embedding", "anomaly_score", "root_cause", "timestamp"),
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
    logger.info("Starting background inference loop (every 60s)")
    while True:
        try:
            await run_inference()
        except asyncio.CancelledError:
            logger.info("Background inference loop cancelled")
            break
        except Exception as e:
            logger.error(f"Error in background inference loop: {e}", exc_info=True)
        await asyncio.sleep(60)

async def start_background_tasks(app):
    import asyncio
    app['inference_task'] = asyncio.create_task(inference_loop(app))

async def cleanup_background_tasks(app):
    app['inference_task'].cancel()
    import asyncio
    await asyncio.gather(app['inference_task'], return_exceptions=True)

async def get_snapshots_handler(request):
    try:
        spanner_client = spanner.Client()
        instance = spanner_client.instance(SPANNER_INSTANCE)
        database = instance.database(SPANNER_DATABASE)
        
        query = "SELECT DISTINCT timestamp FROM NodeEmbedding ORDER BY timestamp DESC LIMIT 100"
        
        snapshots = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query)
            for row in results:
                # Spanner returns datetime objects
                ts = row[0]
                if ts:
                    snapshots.append(ts.isoformat())
                    
        return web.json_response({"snapshots": snapshots})
    except Exception as e:
        logger.error(f"Failed to fetch snapshots: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

async def get_anomalies_handler(request):
    try:
        limit = int(request.query.get('limit', 50))
        timestamp_str = request.query.get('timestamp')
        
        spanner_client = spanner.Client()
        instance = spanner_client.instance(SPANNER_INSTANCE)
        database = instance.database(SPANNER_DATABASE)
        
        params = {"limit": limit}
        param_types = {"limit": spanner.param_types.INT64}
        
        if timestamp_str:
            # Parse timestamp
            # Python 3.7+ fromisoformat handles simple ISO strings, but might fail on Z suffix or others if not careful.
            # Spanner expects passing a timestamp param or string? 
            # Spanner params can take datetime objects.
            try:
                # Handle 'Z' if present
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1] + '+00:00'
                ts = datetime.datetime.fromisoformat(timestamp_str)
                params["timestamp"] = ts
                param_types["timestamp"] = spanner.param_types.TIMESTAMP
                
                query = """
                    SELECT e.node_id, e.node_type, e.anomaly_score, e.root_cause, 
                           COALESCE(r.name, i.name) as name, e.timestamp
                    FROM NodeEmbedding e
                    LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                    LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                    WHERE e.timestamp = @timestamp
                    ORDER BY e.anomaly_score DESC
                    LIMIT @limit
                """
            except ValueError:
                 return web.json_response({'error': 'Invalid timestamp format'}, status=400)
        else:
            # Latest
            query = """
                SELECT e.node_id, e.node_type, e.anomaly_score, e.root_cause, 
                       COALESCE(r.name, i.name) as name, e.timestamp
                FROM NodeEmbedding e
                LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE e.timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
                ORDER BY e.anomaly_score DESC
                LIMIT @limit
            """
            
        anomalies = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=params, param_types=param_types)
            for row in results:
                # row: node_id, node_type, anomaly_score, root_cause, name, timestamp
                anomalies.append({
                    "node_id": row[0],
                    "node_type": row[1],
                    "anomaly_score": row[2],
                    "root_cause": row[3], # JSON/dict
                    "name": row[4] if row[4] else "Unknown",
                    "timestamp": row[5].isoformat() if row[5] else None
                })
                
        return web.json_response({"anomalies": anomalies})
            
    except Exception as e:
        logger.error(f"Failed to fetch anomalies: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

if __name__ == "__main__":
    # Load model on start
    load_model()
    
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
    inference_route = app.router.add_post('/inference', inference_handler)
    snapshots_route = app.router.add_get('/snapshots', get_snapshots_handler)
    anomalies_route = app.router.add_get('/anomalies', get_anomalies_handler)
    
    # Add CORS to API routes
    cors.add(inference_route)
    cors.add(snapshots_route)
    cors.add(anomalies_route)

    logger.info("serving gnn...")
    port = int(os.environ.get('PORT', 8082))
    web.run_app(app, port=port)
