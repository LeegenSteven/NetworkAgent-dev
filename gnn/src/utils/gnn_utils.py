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

# Constants
HIDDEN_CHANNELS = 64
OUT_CHANNELS = 32
NUM_HEADS = 4
NUM_LAYERS = 2

# Mapping of node types to feature names for explainability
FEATURE_MAP = {
    "Router": ["RIB Size", "Config (Semantic)"],
    "Firewall": ["Active Sessions", "Drop Rate", "Config (Semantic)"],
    "Switch": ["Config (Semantic)"],
    "Interface": ["Errors", "Utilization"],
    "Flow": ["Packet Count", "Latency", "Jitter"]
}

def explain_node_anomaly(node_type, original_x, reconstructed_x):
    """
    Decomposes reconstruction error into per-feature contributions.
    Returns the feature name with the highest error.
    """
    if node_type not in FEATURE_MAP:
        return "Unknown"
    
    # Calculate squared error per feature
    # original_x shape: (dim,), reconstructed_x shape: (dim,)
    errors = (original_x - reconstructed_x) ** 2
    
    # Map back to feature labels
    labels = FEATURE_MAP[node_type]
    
    # If it's a device with NetBERT embedding, we collapse the 768-dim embedding into one "Config" score
    if node_type in ["Router", "Firewall", "Switch"]:
        # Numeric features come first
        num_numeric = len(labels) - 1
        numeric_errors = errors[:num_numeric]
        config_error = errors[num_numeric:].mean()
        
        collapsed_errors = torch.cat([numeric_errors, config_error.unsqueeze(0)])
        max_idx = torch.argmax(collapsed_errors).item()
        return labels[max_idx]
    else:
        # For Interface and Flow, all features are numeric and mapped 1-to-1
        max_idx = torch.argmax(errors).item()
        return labels[max_idx]

class THGAT(nn.Module):
    def __init__(self, metadata, hidden_channels, out_channels, num_heads, num_layers):
        super().__init__()
        self.metadata = metadata
        node_types, edge_types = metadata
        
        # 1. Feature Alignment (Projections)
        self.lin_dict = nn.ModuleDict()
        
        # 2. Spatial Layer: HGT
        self.convs = nn.ModuleList()
        for _ in range(num_layers):
            conv = HGTConv(hidden_channels, hidden_channels, metadata, num_heads)
            self.convs.append(conv)

        # 3. Temporal Layer: GRU
        self.gru_dict = nn.ModuleDict()
        for node_type in node_types:
            self.gru_dict[node_type] = nn.GRU(hidden_channels, hidden_channels, batch_first=True)

        # 4. Decoder (for Reconstruction)
        self.decoder_dict = nn.ModuleDict()
        
    def set_input_dims(self, input_dims):
        """Initialize projection layers and decoders based on input feature dimensions."""
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type] = Linear(dim, HIDDEN_CHANNELS)
            self.decoder_dict[node_type] = Linear(HIDDEN_CHANNELS, dim)

    def forward(self, x_dict, edge_index_dict, state_dict=None):
        # 1. Project inputs - only include node types that have data
        h_dict = {}
        for node_type, x in x_dict.items():
            if node_type in self.lin_dict:
                h_dict[node_type] = self.lin_dict[node_type](x).relu()
        
        # 2. Filter edge_index_dict to only include edges between node types that have data
        filtered_edge_index_dict = {}
        for edge_type, edge_index in edge_index_dict.items():
            src_type, rel, dst_type = edge_type
            if src_type in h_dict and dst_type in h_dict:
                filtered_edge_index_dict[edge_type] = edge_index
        
        # Debugging
        # print(f"DEBUG: h_dict keys: {list(h_dict.keys())}")
        # print(f"DEBUG: filtered keys: {list(filtered_edge_index_dict.keys())}")
        
        # 3. Spatial Convolution (HGT)
        for conv in self.convs:
            out_dict = conv(h_dict, filtered_edge_index_dict)
            # Update h_dict with new representations, preserving those that weren't updated (e.g. source-only nodes)
            for node_type, h in out_dict.items():
                h_dict[node_type] = h
            
        # 3. Temporal Update (GRU)
        new_state_dict = {}
        out_dict = {}
        
        for node_type, h in h_dict.items():
            h_in = h.unsqueeze(1) 
            h_prev = state_dict[node_type] if state_dict and node_type in state_dict else None
            out, h_next = self.gru_dict[node_type](h_in, h_prev)
            out_dict[node_type] = out.squeeze(1)
            new_state_dict[node_type] = h_next
            
        # 4. Decode
        recon_dict = {}
        for node_type, h in out_dict.items():
            if node_type in self.decoder_dict:
                recon_dict[node_type] = self.decoder_dict[node_type](h)
            
        return recon_dict, new_state_dict

class GraphBuilder:
    def __init__(self, scaler_path="scalers.pkl"):
        self.scaler_path = scaler_path
        self.scalers = {}
        self.tokenizer = None
        self.text_model = None
        self.text_embed_dim = 768
        self.global_id_map = {"Router": {}, "Switch": {}, "Firewall": {}, "Interface": {}, "Flow": {}}
        
    def init_netbert(self):
        print("Initializing NetBERT...")
        try:
            self.tokenizer = AutoTokenizer.from_pretrained("antoinelouis/netbert")
            self.text_model = AutoModel.from_pretrained("antoinelouis/netbert")
            self.text_model.eval()
            self.text_embed_dim = self.text_model.config.hidden_size
        except Exception as e:
            print(f"Warning: Could not load NetBERT ({e}). Using dummy embeddings.")
            self.text_model = None

    def get_config_embedding(self, text):
        if self.text_model is None:
            return np.zeros(self.text_embed_dim)
        
        try:
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, padding=True, max_length=128)
            with torch.no_grad():
                outputs = self.text_model(**inputs)
            embedding = outputs.last_hidden_state[:, 0, :].squeeze().numpy()
            
            # Check for NaN
            if np.isnan(embedding).any():
                print(f"Warning: NaN in text embedding, using zeros")
                return np.zeros(self.text_embed_dim)
            
            return embedding
        except Exception as e:
            print(f"Warning: Error getting text embedding ({e}), using zeros")
            return np.zeros(self.text_embed_dim)

    def fit_scalers(self, snapshot_objects):
        all_metrics = {
            "Router": {"rib_size": [], "bgp_prefixes": []},
            "Firewall": {"active_sessions": [], "drop_rate": []},
            "Interface": {"errors": [], "utilization": []},
            "Flow": {"packet_count": [], "latency": [], "jitter": []}
        }
        
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                if ntype == "Router":
                    all_metrics["Router"]["rib_size"].append(node.get("rib_size", 0))
                    all_metrics["Router"]["bgp_prefixes"].append(node.get("bgp_prefixes", 0))
                elif ntype == "Firewall":
                    all_metrics["Firewall"]["active_sessions"].append(node.get("active_sessions", 0))
                    all_metrics["Firewall"]["drop_rate"].append(node.get("drop_rate", 0))
                elif ntype == "Interface":
                    all_metrics["Interface"]["errors"].append(node.get("errors", 0))
                    all_metrics["Interface"]["utilization"].append(node.get("utilization", 0))
                elif ntype == "Flow":
                    all_metrics["Flow"]["packet_count"].append(node.get("packet_count", 0))
                    all_metrics["Flow"]["latency"].append(node.get("latency", 0))
                    all_metrics["Flow"]["jitter"].append(node.get("jitter", 0))

        for ntype, metrics in all_metrics.items():
            self.scalers[ntype] = {}
            for metric, values in metrics.items():
                if values:
                    scaler = StandardScaler()
                    scaler.fit(np.array(values).reshape(-1, 1))
                    self.scalers[ntype][metric] = scaler
        
        # Build global ID map
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                if nid not in self.global_id_map[ntype]:
                    self.global_id_map[ntype][nid] = len(self.global_id_map[ntype])
                    
    def save_scalers(self):
        joblib.dump({"scalers": self.scalers, "id_map": self.global_id_map}, self.scaler_path)
        
    def load_scalers(self):
        if os.path.exists(self.scaler_path):
            data = joblib.load(self.scaler_path)
            self.scalers = data["scalers"]
            self.global_id_map = data["id_map"]
            return True
        return False

    def process_snapshot(self, data):
        hetero_data = HeteroData()
        
        # Containers for features
        features_dict = {}
        input_dims = {}
        
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            if count == 0: continue
            
            dim = 0
            if ntype == "Router": dim = 1 + self.text_embed_dim
            elif ntype == "Firewall": dim = 2 + self.text_embed_dim
            elif ntype == "Switch": dim = self.text_embed_dim
            elif ntype == "Interface": dim = 2
            elif ntype == "Flow": dim = 3
            
            features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
            input_dims[ntype] = dim

        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            if nid not in self.global_id_map[ntype]: continue # Skip unknown nodes during inference?
            
            idx = self.global_id_map[ntype][nid]
            vec = []
            
            # Numeric
            if ntype == "Router":
                val = self.scalers["Router"]["rib_size"].transform([[node.get("rib_size",0)]])[0][0]
                vec.append(val)
            elif ntype == "Firewall":
                v1 = self.scalers["Firewall"]["active_sessions"].transform([[node.get("active_sessions",0)]])[0][0]
                v2 = self.scalers["Firewall"]["drop_rate"].transform([[node.get("drop_rate",0)]])[0][0]
                vec.extend([v1, v2])
            elif ntype == "Interface":
                v1 = self.scalers["Interface"]["errors"].transform([[node.get("errors",0)]])[0][0]
                v2 = self.scalers["Interface"]["utilization"].transform([[node.get("utilization",0)]])[0][0]
                vec.extend([v1, v2])
            elif ntype == "Flow":
                v1 = self.scalers["Flow"]["packet_count"].transform([[node.get("packet_count",0)]])[0][0]
                v2 = self.scalers["Flow"]["latency"].transform([[node.get("latency",0)]])[0][0]
                v3 = self.scalers["Flow"]["jitter"].transform([[node.get("jitter",0)]])[0][0]
                vec.extend([v1, v2, v3])
                
            # Text
            if ntype in ["Router", "Firewall", "Switch"]:
                config_text = node.get("config", "")
                text_emb = self.get_config_embedding(config_text)
                vec.extend(text_emb)
            
            features_dict[ntype][idx] = np.array(vec)

        # Convert features to tensors and check for NaN
        for ntype, feat_array in features_dict.items():
            # Check for NaN in feature array
            if np.isnan(feat_array).any():
                print(f"Warning: NaN detected in {ntype} features, replacing with zeros")
                feat_array = np.nan_to_num(feat_array, nan=0.0)
            
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            
        # Edges
        edge_indices = {}
        id_to_type = {n["id"]: n["type"] for n in data["nodes"]}
        
        for edge in data["edges"]:
            src, tgt = edge["source"], edge["target"]
            rel = edge["relation"]
            
            if src not in id_to_type or tgt not in id_to_type: continue
            
            src_type = id_to_type[src]
            tgt_type = id_to_type[tgt]
            edge_type = (src_type, rel, tgt_type)
            
            if edge_type not in edge_indices: edge_indices[edge_type] = [[], []]
            
            if src in self.global_id_map[src_type] and tgt in self.global_id_map[tgt_type]:
                src_idx = self.global_id_map[src_type][src]
                tgt_idx = self.global_id_map[tgt_type][tgt]
                
                edge_indices[edge_type][1].append(tgt_idx)
            
        for etype, indices in edge_indices.items():
            hetero_data[etype].edge_index = torch.tensor(indices, dtype=torch.long)
            
        return hetero_data, input_dims

    def process_snapshot_for_surrogate(self, data):
        """
        Process snapshot for surrogate model training/inference.
        CRITICAL: Masks 'response' variables (latency, errors, etc.) to prevent target leakage.
        Only 'control' variables (config, packet_count, topology) are included in x.
        """
        hetero_data = HeteroData()
        
        # Containers for features (SAME dimensions as process_snapshot to keep architecture simple,
        # but we will zero out the response columns)
        features_dict = {}
        input_dims = {}
        
        # Initialize zero arrays
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            if count == 0: continue
            
            # Dimensions must match original process_snapshot so we can reuse consistent model helper methods if needed,
            # OR we can just zero them out.
            # Router: [rib_size, config_emb] -> Mask rib_size
            # Firewall: [active_sessions, drop_rate, config_emb] -> Mask sessions, drop_rate
            # Switch: [config_emb] -> Keep
            # Interface: [errors, utilization] -> Mask ALL (input is effectively empty/id only)
            # Flow: [packet_count, latency, jitter] -> Keep packet_count, mask latency/jitter
            
            dim = 0
            if ntype == "Router": dim = 1 + self.text_embed_dim
            elif ntype == "Firewall": dim = 2 + self.text_embed_dim
            elif ntype == "Switch": dim = self.text_embed_dim
            elif ntype == "Interface": dim = 2
            elif ntype == "Flow": dim = 3
            
            features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
            input_dims[ntype] = dim

        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            if nid not in self.global_id_map[ntype]: continue
            
            idx = self.global_id_map[ntype][nid]
            vec = []
            
            # Numeric - SELECTIVELY INCLUDE ONLY CONTROL VARIABLES
            if ntype == "Router":
                # rib_size is a response variable (changes with routing), so mask it (use 0)
                # But if we treat it as state... actually in this simplified model let's treat it as response.
                vec.append(0.0) 
            elif ntype == "Firewall":
                # active_sessions, drop_rate are responses. Mask them.
                vec.extend([0.0, 0.0])
            elif ntype == "Interface":
                # errors, utilization are responses. Mask them.
                vec.extend([0.0, 0.0])
            elif ntype == "Flow":
                # packet_count is a CONTROL variable (we set traffic demand). Keep it.
                # latency, jitter are responses. Mask them.
                v1 = self.scalers["Flow"]["packet_count"].transform([[node.get("packet_count",0)]])[0][0]
                vec.extend([v1, 0.0, 0.0])
                
            # Text - Config is a CONTROL variable. Keep it.
            if ntype in ["Router", "Firewall", "Switch"]:
                config_text = node.get("config", "")
                text_emb = self.get_config_embedding(config_text)
                vec.extend(text_emb)
            
            features_dict[ntype][idx] = np.array(vec)

        # Convert features to tensors
        for ntype, feat_array in features_dict.items():
            if np.isnan(feat_array).any():
                feat_array = np.nan_to_num(feat_array, nan=0.0)
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            
        # Edges (same as before)
        edge_indices = {}
        id_to_type = {n["id"]: n["type"] for n in data["nodes"]}
        
        for edge in data["edges"]:
            src, tgt = edge["source"], edge["target"]
            rel = edge["relation"]
            if src not in id_to_type or tgt not in id_to_type: continue
            
            src_type = id_to_type[src]
            tgt_type = id_to_type[tgt]
            edge_type = (src_type, rel, tgt_type)
            
            if edge_type not in edge_indices: edge_indices[edge_type] = [[], []]
            
            if src in self.global_id_map[src_type] and tgt in self.global_id_map[tgt_type]:
                src_idx = self.global_id_map[src_type][src]
                tgt_idx = self.global_id_map[tgt_type][tgt]
                edge_indices[edge_type][0].append(src_idx)
                edge_indices[edge_type][1].append(tgt_idx)
            
        for etype, indices in edge_indices.items():
            hetero_data[etype].edge_index = torch.tensor(indices, dtype=torch.long)
            
        return hetero_data, input_dims
