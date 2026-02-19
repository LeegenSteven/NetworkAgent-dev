
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
INTERVAL_MINUTES = 1
NUM_HEADS = 4
NUM_LAYERS = 2

# Shared Environment Configuration
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "networktopology-db")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts")


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
    Returns a dict with detailed explanation.
    """
    logger.debug(f"Explaining anomaly for node type: {node_type}")
    
    if node_type not in FEATURE_MAP:
        logger.warning(f"Unknown node type: {node_type}")
        return {"error": "Unknown node type"}
    
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
        error_value = float(collapsed_errors[max_idx].item())
        
        logger.info(f"Node type '{node_type}' anomaly attributed to: {feature_label} (error={error_value:.6f})")
        
        return {
            "primary_feature": feature_label,
            "error_value": error_value,
            "feature_errors": {
                labels[0]: float(collapsed_errors[0].item()),
                labels[1]: float(collapsed_errors[1].item())
            }
        }
    else:
        # Interface: All numeric
        max_idx = torch.argmax(errors).item()
        feature_label = labels[max_idx]
        error_value = float(errors[max_idx].item())
        
        logger.info(f"Node type '{node_type}' anomaly attributed to: {feature_label} (error={error_value:.6f})")
        
        # Build feature errors dict
        feature_errors = {}
        for i, label in enumerate(labels):
            if i < len(errors):
                feature_errors[label] = float(errors[i].item())
        
        return {
            "primary_feature": feature_label,
            "error_value": error_value,
            "feature_errors": feature_errors
        }

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
        # Log which input dim corresponds to which feature set
        # Router: 1 + 128 = 129
        # Interface: 4

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
        # Using structured encoding instead of NetBERT
        # Dimensions: 128 hash buckets for robust feature hashing
        self.text_embed_dim = 128
        logger.info(f"Using Structured Config Encoder with dimension: {self.text_embed_dim}")
        
    def _parse_vyos_commands(self, config_text):
        """
        Extracts key configuration items from VyOS text commands.
        Returns a list of feature strings like 'rt_import:65035:1030', 'neighbor:10.0.0.1'.
        """
        features = []
        
        # Split into lines
        if not config_text:
            return features
            
        lines = config_text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            parts = line.split()
            
            # Pattern 1: Route Targets (Import/Export)
            # set vrf name BLUE_SPOKE protocols bgp address-family ipv4-unicast route-target vpn import '65035:9999'
            if "route-target" in line and "vpn" in line:
                try:
                    # Extract the target (last element usually)
                    target = parts[-1].strip("'\"")
                    if "import" in line:
                        features.append(f"rt_import:{target}")
                    elif "export" in line:
                        features.append(f"rt_export:{target}")
                except:
                    pass
                    
            # Pattern 2: BGP Neighbors
            # set protocols bgp neighbor 10.0.0.1 ...
            elif "protocols bgp neighbor" in line:
                try:
                    # 'set', 'protocols', 'bgp', 'neighbor', '10.0.0.1', ...
                    idx = parts.index("neighbor")
                    if idx + 1 < len(parts):
                        neighbor_ip = parts[idx + 1]
                        features.append(f"bgp_neighbor:{neighbor_ip}")
                except:
                    pass
            
            # Pattern 3: Interfaces
            # set interfaces ethernet eth1 address 172.16.90.2/24
            elif "set interfaces" in line and "address" in line:
                try:
                    # simplistic extraction
                    # set interfaces ethernet eth1 address 172.16.90.2/24
                    if "ethernet" in line:
                        idx = parts.index("ethernet")
                        if idx + 1 < len(parts):
                            eth_name = parts[idx + 1]
                            features.append(f"interface:{eth_name}")
                except:
                    pass
                    
            # Pattern 4: VRF Definition
            # set vrf name BLUE_SPOKE table 200
            elif "set vrf name" in line:
                try:
                    idx = parts.index("name")
                    if idx + 1 < len(parts):
                        vrf_name = parts[idx + 1]
                        features.append(f"vrf:{vrf_name}")
                except:
                    pass

        return features

    def get_config_embedding(self, text):
        """
        Generates a fixed-size embedding using hashing of configuration features.
        Robust to new/unseen values.
        """
        # Ensure text is valid
        if text is None or text == "":
            return np.zeros(self.text_embed_dim)
        
        # Extract content (Logic copied from previous fix)
        content_to_embed = text
        data_dict = None
        
        # 1. Normalize input
        if isinstance(text, (dict, list)):
            data_dict = text
        elif hasattr(text, '__class__') and 'JsonObject' in text.__class__.__name__:
             try: data_dict = dict(text)
             except: pass
        elif isinstance(text, str):
            try:
                if text.strip().startswith('{'): data_dict = json.loads(text)
            except: pass
        
        # 2. Extract specific fields
        if isinstance(data_dict, dict):
            if 'status' in data_dict and isinstance(data_dict['status'], dict) and 'applied_config' in data_dict['status']:
                content_to_embed = data_dict['status']['applied_config']
            elif 'spec' in data_dict:
                # Fallback: Convert spec to something resembling commands or just string
                # Ideally we'd parse spec too, but for now stringify
                content_to_embed = json.dumps(data_dict['spec'])
            else:
                content_to_embed = json.dumps(data_dict)
        
        # 3. Ensure string
        if not isinstance(content_to_embed, str):
            content_to_embed = str(content_to_embed)

        # --- Structured Hashing Encoding ---
        # 1. Parse features
        features = self._parse_vyos_commands(content_to_embed)
        
        # 2. Initialize zero vector
        embedding = np.zeros(self.text_embed_dim)
        
        # 3. Hash features into buckets
        # This is a simple "Hashing Vectorizer" / "Multi-hot Encoding"
        import hashlib
        
        if not features:
            # If parsing failed (e.g. JSON spec instead of commands), 
            # fallback to hashing the raw tokens of the string
            # to ensure we still detect changes
            features = content_to_embed.split()
        
        for feature in features:
            # Deterministic hash to index
            # MD5 is stable across runs/platforms
            hash_val = int(hashlib.md5(feature.encode('utf-8')).hexdigest(), 16)
            idx = hash_val % self.text_embed_dim
            
            # Binary presence (or count)
            # Using 1.0 for presence creates a "multi-hot" vector
            embedding[idx] = 1.0
            
        logger.debug(f"Encoded {len(features)} features into {self.text_embed_dim}-dim vector")
        return embedding

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
                    all_metrics["Interface"]["rx"].append(float(node.get("rx") or 0.0))
                    all_metrics["Interface"]["tx"].append(float(node.get("tx") or 0.0))
                    all_metrics["Interface"]["errors"].append(float(node.get("errors") or 0.0))

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
        # Router: [State(1)] + [Config(128)] = 129
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
                logger.info(f"processing router {node.get('hostname')}")
                # State
                vec.append(node.get("state", 0.0))
                # Config using structured encoding
                config_text = node.get("config", "")
                vec.extend(self.get_config_embedding(config_text))
                
            elif ntype == "Interface":
                logger.info(f"interface {node.get('device_id')} {node.get('name')}")
                # State
                vec.append(node.get("state", 0.0))

                # Scaled metrics: Errors, Rx, Tx
                # Handle missing scaler gracefully if we fit on empty data
                def get_scaled(metric):
                    val = float(node.get(metric, 0.0) or 0.0)
                    if "Interface" in self.scalers and metric in self.scalers["Interface"]:
                        scaled_val = self.scalers["Interface"][metric].transform([[val]])[0][0]
                        return scaled_val
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
