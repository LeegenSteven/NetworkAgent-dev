
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
    if node_type not in FEATURE_MAP:
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
        return labels[max_idx]
    else:
        # Interface: All numeric
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
        # 1. Project inputs
        h_dict = {}
        for node_type, x in x_dict.items():
            if node_type in self.lin_dict:
                h_dict[node_type] = self.lin_dict[node_type](x).relu()
        
        # 2. Filter edges
        filtered_edge_index_dict = {}
        for edge_type, edge_index in edge_index_dict.items():
            src_type, rel, dst_type = edge_type
            if src_type in h_dict and dst_type in h_dict:
                filtered_edge_index_dict[edge_type] = edge_index
        
        # 3. Spatial Convolution (HGT)
        for conv in self.convs:
            out_dict = conv(h_dict, filtered_edge_index_dict)
            for node_type, h in out_dict.items():
                h_dict[node_type] = h
            
        # 4. Temporal Update (GRU)
        new_state_dict = {}
        out_dict = {}
        
        for node_type, h in h_dict.items():
            h_in = h.unsqueeze(1) 
            h_prev = state_dict[node_type] if state_dict and node_type in state_dict else None
            out, h_next = self.gru_dict[node_type](h_in, h_prev)
            out_dict[node_type] = out.squeeze(1)
            new_state_dict[node_type] = h_next
            
        # 5. Decode
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
        self.global_id_map = {
            "PE Router": {}, "P Router": {}, "CE Router": {}, "Interface": {}
        }
        
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
            if np.isnan(embedding).any(): return np.zeros(self.text_embed_dim)
            return embedding
        except Exception:
            return np.zeros(self.text_embed_dim)

    def fit_scalers(self, snapshot_objects):
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
        
        # Build global ID map
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                if ntype in self.global_id_map:
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
        features_dict = {}
        input_dims = {}
        
        # Define dimensions
        # Router: [State(1)] + [Config(768)] = 769
        # Interface: [State(1), Errors(1), Rx(1), Tx(1)] = 4
        
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            if count == 0: continue
            
            dim = 0
            if "Router" in ntype: dim = 1 + self.text_embed_dim
            elif ntype == "Interface": dim = 4
            
            features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
            input_dims[ntype] = dim

        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            if ntype not in self.global_id_map or nid not in self.global_id_map[ntype]: continue
            
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

        for ntype, feat_array in features_dict.items():
            if np.isnan(feat_array).any():
                feat_array = np.nan_to_num(feat_array, nan=0.0)
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            
        # Edge Processing
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
