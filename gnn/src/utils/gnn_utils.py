
import json
import os
import glob
import numpy as np
import torch
import torch.nn as nn
from torch_geometric.data import HeteroData
from torch_geometric.nn import HGTConv, Linear
from transformers import AutoTokenizer, AutoModel
from sklearn.preprocessing import StandardScaler
import joblib
import logging

logger = logging.getLogger(__name__)

# Constants
HIDDEN_CHANNELS = 64
OUT_CHANNELS = 32
NUM_HEADS = 4
NUM_LAYERS = 2

# Mapping of node types to feature names for explainability
# Updated to User Specs:
# PE/P/CE Router: [State, Config (Semantic)]
# Interface: [State, Errors, Rx, Tx]
FEATURE_MAP = {
    "PE Router": ["State", "Config (Semantic)"],
    "P Router": ["State", "Config (Semantic)"],
    "CE Router": ["State", "Config (Semantic)"],
    "Interface": ["State", "Errors", "Rx", "Tx"]
}

def explain_node_anomaly(node_type, original_x, reconstructed_x):
    """
    Decomposes reconstruction error into per-feature contributions.
    """
    logger.debug(f"Explaining anomaly for node type: {node_type}")
    
    if node_type not in FEATURE_MAP:
        logger.warning(f"Unknown node type: {node_type}")
        return "Unknown"
    
    errors = (original_x - reconstructed_x) ** 2
    labels = FEATURE_MAP[node_type]
    
    # Routers have embedding at the end
    if node_type in ["PE Router", "P Router", "CE Router"]:
        # Numeric features: State (1 dim)
        # Config embedding: Remainder
        numeric_errors = errors[:1] 
        config_error = errors[1:].mean()
        
        collapsed_errors = torch.cat([numeric_errors, config_error.unsqueeze(0)])
        max_idx = torch.argmax(collapsed_errors).item()
        feature_label = labels[max_idx]
        logger.info(f"Node type '{node_type}' anomaly attributed to: {feature_label}")
        return feature_label
    else:
        # Interface: All numeric
        max_idx = torch.argmax(errors).item()
        feature_label = labels[max_idx]
        logger.info(f"Node type '{node_type}' anomaly attributed to: {feature_label}")
        return feature_label

class THGAT(nn.Module):
    def __init__(self, metadata, hidden_channels, out_channels, num_heads, num_layers):
        super().__init__()
        logger.info(f"Initializing THGAT model with hidden_channels={hidden_channels}, "
                   f"out_channels={out_channels}, num_heads={num_heads}, num_layers={num_layers}")
        
        self.metadata = metadata
        node_types, edge_types = metadata
        logger.debug(f"Node types: {node_types}")
        logger.debug(f"Edge types: {len(edge_types)} edge types")
        
        # 1. Feature Alignment (Projections)
        self.lin_dict = nn.ModuleDict()
        
        # 2. Spatial Layer: HGT
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            conv = HGTConv(hidden_channels, hidden_channels, metadata, num_heads)
            self.convs.append(conv)
            logger.debug(f"Added HGT convolution layer {i+1}/{num_layers}")

        # 3. Temporal Layer: GRU
        self.gru_dict = nn.ModuleDict()
        for node_type in node_types:
            self.gru_dict[node_type] = nn.GRU(hidden_channels, hidden_channels, batch_first=True)
            logger.debug(f"Added GRU layer for node type: {node_type}")
    
        # 4. Decoder (for Reconstruction)
        self.decoder_dict = nn.ModuleDict()
        logger.info("THGAT model initialization complete")
        
    def set_input_dims(self, input_dims):
        """Initialize projection layers and decoders based on input feature dimensions."""
        logger.info(f"Setting input dimensions for {len(input_dims)} node types")
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type] = Linear(dim, HIDDEN_CHANNELS)
            self.decoder_dict[node_type] = Linear(HIDDEN_CHANNELS, dim)
            logger.debug(f"Node type '{node_type}': input_dim={dim}, hidden_dim={HIDDEN_CHANNELS}")
        logger.info("Input dimensions set successfully")

    def forward(self, x_dict, edge_index_dict, state_dict=None):
        logger.debug(f"Forward pass - Processing {len(x_dict)} node types")
        
        # 1. Project inputs
        h_dict = {}
        for node_type, x in x_dict.items():
            if node_type in self.lin_dict:
                h_dict[node_type] = self.lin_dict[node_type](x).relu()
                logger.debug(f"Projected {node_type}: {x.shape} -> {h_dict[node_type].shape}")
        
        # 2. Filter edges
        filtered_edge_index_dict = {}
        for edge_type, edge_index in edge_index_dict.items():
            src_type, rel, dst_type = edge_type
            if src_type in h_dict and dst_type in h_dict:
                filtered_edge_index_dict[edge_type] = edge_index
        logger.debug(f"Filtered edges: {len(filtered_edge_index_dict)}/{len(edge_index_dict)} edge types retained")
        
        # 3. Spatial Convolution (HGT)
        for i, conv in enumerate(self.convs):
            out_dict = conv(h_dict, filtered_edge_index_dict)
            for node_type, h in out_dict.items():
                h_dict[node_type] = h
            logger.debug(f"HGT layer {i+1}/{len(self.convs)} completed")
            
        # 4. Temporal Update (GRU)
        new_state_dict = {}
        out_dict = {}
        
        for node_type, h in h_dict.items():
            h_in = h.unsqueeze(1) 
            h_prev = state_dict[node_type] if state_dict and node_type in state_dict else None
            out, h_next = self.gru_dict[node_type](h_in, h_prev)
            out_dict[node_type] = out.squeeze(1)
            new_state_dict[node_type] = h_next
            logger.debug(f"GRU update for {node_type}: output shape {out_dict[node_type].shape}")
            
        # 5. Decode
        recon_dict = {}
        for node_type, h in out_dict.items():
            if node_type in self.decoder_dict:
                recon_dict[node_type] = self.decoder_dict[node_type](h)
                logger.debug(f"Decoded {node_type}: {h.shape} -> {recon_dict[node_type].shape}")
        
        logger.debug(f"Forward pass complete - reconstructed {len(recon_dict)} node types")
        return recon_dict, new_state_dict

class GraphBuilder:
    def __init__(self, scaler_path="scalers.pkl"):
        logger.info(f"Initializing GraphBuilder with scaler_path: {scaler_path}")
        self.scaler_path = scaler_path
        self.scalers = {}
        self.tokenizer = None
        self.text_model = None
        self.text_embed_dim = 768
        self.global_id_map = {
            "PE Router": {}, "P Router": {}, "CE Router": {}, "Interface": {}
        }
        logger.debug(f"Global ID map initialized with node types: {list(self.global_id_map.keys())}")
        
    def init_netbert(self):
        logger.info("Initializing NetBERT model...")
        try:
            self.tokenizer = AutoTokenizer.from_pretrained("antoinelouis/netbert")
            logger.debug("NetBERT tokenizer loaded successfully")
            self.text_model = AutoModel.from_pretrained("antoinelouis/netbert")
            self.text_model.eval()
            self.text_embed_dim = self.text_model.config.hidden_size
            logger.info(f"NetBERT initialized successfully with embedding dimension: {self.text_embed_dim}")
        except Exception as e:
            logger.warning(f"Could not load NetBERT ({e}). Using dummy embeddings.")
            self.text_model = None

    def get_config_embedding(self, text):
        if self.text_model is None:
            logger.debug("NetBERT model not available, returning zero embedding")
            return np.zeros(self.text_embed_dim)
        
        # Ensure text is a valid string
        if text is None or text == "":
            logger.debug("Empty or None config text, returning zero embedding")
            return np.zeros(self.text_embed_dim)
        
        # Handle different input types
        if not isinstance(text, str):
            # Handle Spanner JsonObject
            if hasattr(text, '__class__') and 'JsonObject' in text.__class__.__name__:
                try:
                    # Convert JsonObject to JSON string
                    text = json.dumps(dict(text))
                    logger.debug("Converted Spanner JsonObject to JSON string")
                except Exception as e:
                    logger.warning(f"Failed to convert JsonObject to JSON: {e}, using str() instead")
                    text = str(text)
            # Handle dict or other objects
            elif isinstance(text, dict):
                text = json.dumps(text)
                logger.debug("Converted dict to JSON string")
            else:
                logger.warning(f"Config text is not a string (type: {type(text)}), converting to string")
                text = str(text)
        
        try:
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, padding=True, max_length=128)
            with torch.no_grad():
                outputs = self.text_model(**inputs)
            embedding = outputs.last_hidden_state[:, 0, :].squeeze().numpy()
            if np.isnan(embedding).any():
                logger.warning("NaN values detected in embedding, replacing with zero embedding")
                return np.zeros(self.text_embed_dim)
            logger.debug(f"Generated config embedding of shape: {embedding.shape}")
            return embedding
        except Exception as e:
            logger.error(f"Error generating config embedding for text type {type(text)}: {e}")
            return np.zeros(self.text_embed_dim)

    def fit_scalers(self, snapshot_objects):
        logger.info(f"Fitting scalers on {len(snapshot_objects)} snapshot objects")
        
        # We only really need to scale 'rx', 'tx', 'errors'. 'state' is usually 0/1.
        all_metrics = {
            "Interface": {"rx": [], "tx": [], "errors": []}
        }
        
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                if ntype == "Interface":
                    all_metrics["Interface"]["rx"].append(node.get("rx", 0.0))
                    all_metrics["Interface"]["tx"].append(node.get("tx", 0.0))
                    all_metrics["Interface"]["errors"].append(node.get("errors", 0.0))

        for ntype, metrics in all_metrics.items():
            self.scalers[ntype] = {}
            for metric, values in metrics.items():
                if values:
                    scaler = StandardScaler()
                    # Reshape for fit
                    scaler.fit(np.array(values).reshape(-1, 1))
                    self.scalers[ntype][metric] = scaler
                    logger.debug(f"Fitted scaler for {ntype}.{metric} with {len(values)} values "
                               f"(mean={scaler.mean_[0]:.4f}, std={np.sqrt(scaler.var_[0]):.4f})")
        
        # Build global ID map
        logger.info("Building global ID map")
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                if ntype in self.global_id_map:
                    if nid not in self.global_id_map[ntype]:
                        self.global_id_map[ntype][nid] = len(self.global_id_map[ntype])
        
        for ntype, id_map in self.global_id_map.items():
            logger.info(f"Global ID map for {ntype}: {len(id_map)} unique nodes")

    def save_scalers(self):
        logger.info(f"Saving scalers and ID map to {self.scaler_path}")
        joblib.dump({"scalers": self.scalers, "id_map": self.global_id_map}, self.scaler_path)
        logger.info("Scalers saved successfully")
        
    def load_scalers(self):
        if os.path.exists(self.scaler_path):
            logger.info(f"Loading scalers from {self.scaler_path}")
            data = joblib.load(self.scaler_path)
            self.scalers = data["scalers"]
            self.global_id_map = data["id_map"]
            logger.info("Scalers loaded successfully")
            for ntype, id_map in self.global_id_map.items():
                logger.debug(f"Loaded ID map for {ntype}: {len(id_map)} nodes")
            return True
        logger.warning(f"Scaler file not found: {self.scaler_path}")
        return False

    def process_snapshot(self, data):
        logger.info("Processing network snapshot")
        hetero_data = HeteroData()
        features_dict = {}
        input_dims = {}
        
        # Define dimensions
        # Router: [State(1)] + [Config(768)] = 769
        # Interface: [State(1), Errors(1), Rx(1), Tx(1)] = 4
        
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            if count == 0:
                logger.debug(f"Skipping {ntype}: no nodes in ID map")
                continue
            
            dim = 0
            if "Router" in ntype: dim = 1 + self.text_embed_dim
            elif ntype == "Interface": dim = 4
            
            features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
            input_dims[ntype] = dim
            logger.debug(f"Initialized feature array for {ntype}: shape ({count}, {dim})")

        node_count = 0
        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            if ntype not in self.global_id_map or nid not in self.global_id_map[ntype]:
                logger.debug(f"Skipping unknown node: {nid} of type {ntype}")
                continue
            
            idx = self.global_id_map[ntype][nid]
            vec = []
            
            if "Router" in ntype:
                # State
                vec.append(node.get("state", 0.0))
                # Config using NetBERT
                config_text = node.get("config", "")
                vec.extend(self.get_config_embedding(config_text))
                
            elif ntype == "Interface":
                # State
                vec.append(node.get("state", 0.0))
                # Scaled metrics: Errors, Rx, Tx
                # Handle missing scaler gracefully if we fit on empty data
                def get_scaled(metric):
                    val = node.get(metric, 0.0)
                    if "Interface" in self.scalers and metric in self.scalers["Interface"]:
                        return self.scalers["Interface"][metric].transform([[val]])[0][0]
                    return val # Fallback unscaled
                    
                vec.append(get_scaled("errors"))
                vec.append(get_scaled("rx"))
                vec.append(get_scaled("tx"))
            
            features_dict[ntype][idx] = np.array(vec)
            node_count += 1

        logger.info(f"Processed {node_count} nodes")
        
        for ntype, feat_array in features_dict.items():
            if np.isnan(feat_array).any():
                logger.warning(f"NaN values detected in {ntype} features, replacing with zeros")
                feat_array = np.nan_to_num(feat_array, nan=0.0)
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            logger.debug(f"Created tensor for {ntype}: shape {hetero_data[ntype].x.shape}")
            
        # Edge Processing
        edge_indices = {}
        id_to_type = {n["id"]: n["type"] for n in data["nodes"]}
        
        edge_count = 0
        for edge in data["edges"]:
            src, tgt = edge["source"], edge["target"]
            rel = edge["relation"]
            if src not in id_to_type or tgt not in id_to_type:
                logger.debug(f"Skipping edge: source or target node not found")
                continue
            
            src_type = id_to_type[src]
            tgt_type = id_to_type[tgt]
            edge_type = (src_type, rel, tgt_type)
            
            if edge_type not in edge_indices: edge_indices[edge_type] = [[], []]
            
            if src in self.global_id_map[src_type] and tgt in self.global_id_map[tgt_type]:
                src_idx = self.global_id_map[src_type][src]
                tgt_idx = self.global_id_map[tgt_type][tgt]
                edge_indices[edge_type][0].append(src_idx)
                edge_indices[edge_type][1].append(tgt_idx)
                edge_count += 1
        
        logger.info(f"Processed {edge_count} edges across {len(edge_indices)} edge types")
            
        for etype, indices in edge_indices.items():
            hetero_data[etype].edge_index = torch.tensor(indices, dtype=torch.long)
            logger.debug(f"Edge type {etype}: {len(indices[0])} edges")
        
        logger.info("Snapshot processing complete")
        return hetero_data, input_dims
