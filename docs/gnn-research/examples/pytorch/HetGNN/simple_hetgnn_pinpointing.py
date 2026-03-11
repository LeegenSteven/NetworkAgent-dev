"""
Simple HetGNN Failure Pinpointing — PyTorch / PyG
==================================================
Heterogeneous GNN autoencoder demonstrating typed branch anomaly scoring.

    pip install torch torch-geometric
    python simple_hetgnn_pinpointing.py

Topology (subset of hub-and-spoke lab):
    [Router: pe1] ──has_interface──▶ [Interface: pe1_eth1]
    [Router: p1 ] ──has_interface──▶ [Interface: p1_eth3 ]
                                            ↕  connects_to
    [Router: pe1] ──has_bgp──────▶  [BGPSession: pe1_bgp]

This is a SIMPLIFIED version of gnn/src/model/hetgnn.py.
Structural changes from production:
  - Synthetic in-memory data instead of Spanner
  - hidden_channels=32 instead of 64
  - 2 node types used for faults (no OSPF_Adjacency, Config sub-types)

Production code reference: gnn/src/model/hetgnn.py
  - Same HeteroConv + SAGEConv architecture
  - Same metadata tuple: (node_types, edge_types)
  - Same set_input_dims() pattern for lazy weight init
  - Same forward() signature: x_dict, edge_index_dict → (recon_dict, out_embeddings)
  - Same lin_dict / decoder_dict ModuleDict pattern

Key output — branch-level anomaly score:
  Router branch highest    → cpu/mem/ospf fault
  Interface branch highest → MTU mismatch / drops (← Fault 1)
  BGP branch highest       → session teardown     (← Fault 2)
"""

import torch
import torch.nn as nn
import numpy as np
from torch_geometric.nn import HeteroConv, SAGEConv

# ──────────────────────────────────────────────────────────────────────────────
# 1. TOPOLOGY METADATA
#    Same format as production: (node_types_list, edge_types_list)
#    Used to build HeteroConv dicts and RNN dicts.
# ──────────────────────────────────────────────────────────────────────────────

NODE_TYPES = ['router', 'interface', 'bgp_session']

EDGE_TYPES = [
    ('router',    'has_interface', 'interface'),
    ('interface', 'connects_to',   'interface'),
    ('router',    'has_bgp',       'bgp_session'),
]

METADATA = (NODE_TYPES, EDGE_TYPES)

# Edge indices [2, E] — local indices within each node type
# SAGEConv in HeteroConv uses (src_local_idx, dst_local_idx)
EDGE_INDEX_DICT = {
    # pe1(router 0)→pe1_eth1(iface 0),  p1(router 1)→p1_eth3(iface 1)
    ('router', 'has_interface', 'interface'):  torch.tensor([[0, 1], [0, 1]], dtype=torch.long),
    # pe1_eth1(iface 0)↔p1_eth3(iface 1) — bidirectional physical link
    ('interface', 'connects_to', 'interface'): torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
    # pe1(router 0)→pe1_bgp(bgp 0)
    ('router', 'has_bgp', 'bgp_session'):      torch.tensor([[0], [0]], dtype=torch.long),
}

# Node names for reporting
NODE_NAMES = {
    'router':      ['pe1', 'p1'],
    'interface':   ['pe1_eth1', 'p1_eth3'],
    'bgp_session': ['pe1_bgp'],
}

# Feature names per type
FEATURE_NAMES = {
    'router':      ['cpu_percent', 'mem_percent', 'ospf_state'],
    'interface':   ['tx_drops_rate', 'rx_drops_rate', 'mtu_norm'],
    'bgp_session': ['bgp_state', 'pfx_count_norm', 'uptime_norm'],
}

INPUT_DIMS = {nt: len(FEATURE_NAMES[nt]) for nt in NODE_TYPES}  # all 3

# ──────────────────────────────────────────────────────────────────────────────
# 2. HEALTHY BASELINES
# ──────────────────────────────────────────────────────────────────────────────

# Router: cpu, mem, ospf_state (1=Full)
ROUTER_BASELINE = torch.tensor([
    [0.22, 0.30, 1.0],  # pe1
    [0.20, 0.30, 1.0],  # p1
], dtype=torch.float32)

# Interface: tx_drops, rx_drops, mtu_norm (1500/9000 ≈ 0.167)
INTERFACE_BASELINE = torch.tensor([
    [0.01, 0.01, 0.167],  # pe1_eth1
    [0.01, 0.01, 0.167],  # p1_eth3
], dtype=torch.float32)

# BGP: state (1=Established), prefix count/1000, uptime/86400
BGP_BASELINE = torch.tensor([
    [1.0, 0.50, 0.80],  # pe1_bgp: UP, 500 prefixes, ~19h uptime
], dtype=torch.float32)


def generate_normal_snapshots(n: int = 500, noise_std: float = 0.015, seed: int = 42) -> list:
    """Returns list of x_dict snapshots with healthy features + small noise."""
    torch.manual_seed(seed)
    snapshots = []
    for _ in range(n):
        snapshots.append({
            'router':      torch.clamp(ROUTER_BASELINE    + torch.randn(2, 3) * noise_std, 0, 1),
            'interface':   torch.clamp(INTERFACE_BASELINE + torch.randn(2, 3) * noise_std, 0, 1),
            'bgp_session': torch.clamp(BGP_BASELINE       + torch.randn(1, 3) * noise_std, 0, 1),
        })
    return snapshots


def make_fault1_snapshot() -> dict:
    """
    Fault 1 — MTU mismatch on pe1_eth1 (Interface branch fault).
    TX drops spike. MTU deviates. Router and BGP stay healthy.
    Expected: Interface branch scores highest.
    """
    return {
        'router':      ROUTER_BASELINE.clone(),
        'interface':   torch.tensor([[0.75, 0.01, 0.156],   # pe1_eth1: fault
                                     [0.01, 0.01, 0.167]],  # p1_eth3: healthy
                                    dtype=torch.float32),
        'bgp_session': BGP_BASELINE.clone(),
    }


def make_fault2_snapshot() -> dict:
    """
    Fault 2 — pe1_bgp session teardown (BGP branch fault).
    Session flips DOWN, prefix count and uptime reset to zero.
    Router and Interface stay healthy.
    Expected: BGP branch scores highest.
    """
    return {
        'router':      ROUTER_BASELINE.clone(),
        'interface':   INTERFACE_BASELINE.clone(),
        'bgp_session': torch.tensor([[0.0, 0.0, 0.0]], dtype=torch.float32),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. HetGNN AUTOENCODER
#    Direct simplified port of gnn/src/model/hetgnn.py
#
#    Identical patterns:
#      self.convs       nn.ModuleList of HeteroConv(SAGEConv, aggr='sum')
#      self.lin_dict    nn.ModuleDict: node_type → Linear(F_in, H)
#      self.decoder_dict nn.ModuleDict: node_type → Linear(H, F_in)
#      set_input_dims() called after instantiation (lazy init)
#      forward()        x_dict, edge_index_dict → (recon_dict, out_embeddings)
# ──────────────────────────────────────────────────────────────────────────────

class HetGNNAutoencoder(nn.Module):
    """
    Heterogeneous GNN Autoencoder — simplified gnn/src/model/hetgnn.py.

    Each node type has its own:
      - Input projection (lin_dict)
      - Message weights (inside SAGEConv via HeteroConv)
      - Output decoder (decoder_dict)

    The branch with the highest reconstruction error names the fault layer.
    """

    def __init__(
        self,
        metadata: tuple,
        hidden_channels: int = 32,
        num_layers: int = 2,
    ):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers

        # ── Spatial graph convolutions (identical to production) ──────────────
        # SAGEConv(-1, -1, ...) uses lazy init: input size inferred on first call
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            conv_dict = {}
            for edge_type in metadata[1]:
                if i == 0:
                    conv_dict[edge_type] = SAGEConv((-1, -1), hidden_channels)
                else:
                    conv_dict[edge_type] = SAGEConv(hidden_channels, hidden_channels)
            self.convs.append(HeteroConv(conv_dict, aggr='sum'))

        # ── Typed projections and decoders ────────────────────────────────────
        # Populated by set_input_dims() — same pattern as production
        self.lin_dict     = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims: dict) -> None:
        """
        Initialise per-type projection and decoder weights.
        Called once after instantiation when feature dimensions are known.
        Identical to production set_input_dims().
        """
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type]     = nn.Linear(dim, self.hidden_channels)
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(self, x_dict: dict, edge_index_dict: dict) -> tuple:
        """
        Forward pass — identical structure to gnn/src/model/hetgnn.py forward().

        Args:
            x_dict:          {node_type: Tensor[N, F]}
            edge_index_dict: {(src, rel, dst): Tensor[2, E]}

        Returns:
            recon_dict:    {node_type: Tensor[N, F]}  — reconstructed features
            out_embeddings:{node_type: Tensor[N, H]}  — latent embeddings
        """
        # ── Initial typed projection into latent space ────────────────────────
        h_dict = {}
        for nt, x in x_dict.items():
            if x is not None and x.size(0) > 0:
                h_dict[nt] = self.lin_dict[nt](x).relu()

        # ── Filter edges to only those with valid src and dst ─────────────────
        # Mirrors production's filtering logic
        filtered = {
            et: ei
            for et, ei in edge_index_dict.items()
            if et[0] in h_dict and et[2] in h_dict and ei.size(1) > 0
        }

        # ── Heterogeneous message passing (identical to production) ───────────
        for conv in self.convs:
            h_updated = conv(h_dict, filtered)
            for nt in h_dict:
                if nt in h_updated:
                    h_dict[nt] = h_updated[nt].relu()
                # Nodes with no incoming edges keep their previous representation

        # ── Per-type decode ───────────────────────────────────────────────────
        recon_dict     = {}
        out_embeddings = {}
        for nt in x_dict:
            if nt in h_dict:
                out_embeddings[nt] = h_dict[nt]
                recon_dict[nt]     = self.decoder_dict[nt](h_dict[nt])

        return recon_dict, out_embeddings


# ──────────────────────────────────────────────────────────────────────────────
# 4. TRAINING
#    Multi-task loss: (Loss_router + Loss_interface + Loss_bgp) / 3
#    Equal weights here (α=β=γ=1/3) — matches research doc baseline
# ──────────────────────────────────────────────────────────────────────────────

def train(
    model: HetGNNAutoencoder,
    snapshots: list,
    edge_index_dict: dict,
    epochs: int = 100,
    lr: float = 1e-3,
) -> None:
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn   = nn.MSELoss()

    print(f"\n{'='*60}")
    print(f"  Training HetGNN Autoencoder — {epochs} epochs")
    print(f"  Branches: router | interface | bgp_session")
    print(f"  Snapshots: {len(snapshots)}   Hidden: {model.hidden_channels}   Layers: {model.num_layers}")
    print(f"{'='*60}")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0

        for x_dict in snapshots:
            optimiser.zero_grad()
            recon_dict, _ = model(x_dict, edge_index_dict)

            # Per-branch reconstruction loss
            loss_r = loss_fn(recon_dict['router'],      x_dict['router'])
            loss_i = loss_fn(recon_dict['interface'],   x_dict['interface'])
            loss_b = loss_fn(recon_dict['bgp_session'], x_dict['bgp_session'])
            loss   = (loss_r + loss_i + loss_b) / 3.0

            loss.backward()
            optimiser.step()
            total_loss += loss.item()

        if epoch % 20 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs} — loss: {total_loss / len(snapshots):.6f}")

    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────────────────────
# 5. ANOMALY SCORING
# ──────────────────────────────────────────────────────────────────────────────

@torch.no_grad()
def compute_scores(
    model: HetGNNAutoencoder,
    x_dict: dict,
    edge_index_dict: dict,
) -> tuple:
    """
    Per-node and per-branch reconstruction MSE.
    Branch score = max per-node score within that type.
    The branch with the highest score names the fault layer.
    """
    model.eval()
    recon_dict, _ = model(x_dict, edge_index_dict)

    node_scores   = {}
    branch_scores = {}

    for nt in x_dict:
        if nt in recon_dict:
            err = ((recon_dict[nt] - x_dict[nt]) ** 2).mean(dim=1)  # [N]
            node_scores[nt]   = err.numpy()
            branch_scores[nt] = float(err.max())

    return node_scores, branch_scores, {nt: recon_dict[nt].numpy() for nt in recon_dict}


def print_report(fault_name: str, node_scores: dict, branch_scores: dict, recon: dict, actual: dict) -> None:
    print(f"\n{'='*62}")
    print(f"  Fault: {fault_name}")
    print(f"{'='*62}")

    print(f"\n  Per-node scores:")
    for nt in NODE_TYPES:
        if nt in node_scores:
            for i, (name, score) in enumerate(zip(NODE_NAMES[nt], node_scores[nt])):
                print(f"    {nt:<12} {name:<12}  {score:.5f}")

    max_branch = max(branch_scores, key=branch_scores.get)
    print(f"\n  Branch-level diagnosis:")
    for nt in NODE_TYPES:
        if nt in branch_scores:
            marker = "  ⚠️  FAULT LAYER" if nt == max_branch else ""
            print(f"    {nt:<14}  {branch_scores[nt]:.5f}{marker}")

    print(f"\n  → Root layer: {max_branch}")

    # Feature breakdown for most anomalous node in fault branch
    top_node_idx = int(np.argmax(node_scores[max_branch]))
    top_node_name = NODE_NAMES[max_branch][top_node_idx]
    feat_names = FEATURE_NAMES[max_branch]
    act = actual[max_branch][top_node_idx]
    rec = recon[max_branch][top_node_idx]
    errs = (act - rec) ** 2
    feat_rank = np.argsort(errs)[::-1]

    print(f"\n  Feature breakdown for '{top_node_name}' ({max_branch}):")
    print(f"  {'Feature':<18} {'Actual':>8} {'Expected':>9} {'Error²':>8}")
    print(f"  {'-'*48}")
    for f in feat_rank:
        print(f"  {feat_names[f]:<18} {act[f]:>8.4f} {rec[f]:>9.4f} {errs[f]:>8.4f}")
    print(f"\n  Top driver: '{feat_names[feat_rank[0]]}'")


# ──────────────────────────────────────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*62)
    print("  HetGNN Failure Pinpointing — PyTorch / torch_geometric")
    print("  Mirrors: gnn/src/model/hetgnn.py")
    print("  Topology: pe1 + p1 routers, pe1_eth1/p1_eth3 interfaces, pe1_bgp")
    print("="*62)

    # Instantiate model and wire up input dims (production pattern)
    model = HetGNNAutoencoder(metadata=METADATA, hidden_channels=32, num_layers=2)
    model.set_input_dims(INPUT_DIMS)

    # Train on healthy snapshots
    snapshots = generate_normal_snapshots(n=500)
    train(model, snapshots, EDGE_INDEX_DICT, epochs=100, lr=1e-3)

    # Sanity — healthy snapshot
    print("[Sanity] Healthy snapshot (all branches should score low):")
    x_h = generate_normal_snapshots(n=1, seed=99)[0]
    _, br_h, _ = compute_scores(model, x_h, EDGE_INDEX_DICT)
    for nt, s in br_h.items():
        print(f"  {nt:<14}  {s:.6f}")

    # Fault 1 — Interface branch
    print("\n[Fault 1] MTU mismatch on pe1_eth1:")
    x_f1 = make_fault1_snapshot()
    ns1, br1, rec1 = compute_scores(model, x_f1, EDGE_INDEX_DICT)
    print_report(
        "MTU mismatch on pe1_eth1 (Fault 1)",
        ns1, br1,
        {nt: rec1[nt] for nt in rec1},
        {nt: x_f1[nt].numpy() for nt in x_f1},
    )

    # Fault 2 — BGP branch
    print("\n[Fault 2] pe1_bgp session teardown:")
    x_f2 = make_fault2_snapshot()
    ns2, br2, rec2 = compute_scores(model, x_f2, EDGE_INDEX_DICT)
    print_report(
        "pe1_bgp session teardown (Fault 2)",
        ns2, br2,
        {nt: rec2[nt] for nt in rec2},
        {nt: x_f2[nt].numpy() for nt in x_f2},
    )

    # Summary
    print("="*62)
    print("  Summary — HetGNN branch diagnosis")
    print("="*62)
    print(f"  Fault 1 (MTU):     interface {br1.get('interface',0):.4f}  bgp {br1.get('bgp_session',0):.4f}")
    print(f"  Fault 2 (BGP):     interface {br2.get('interface',0):.4f}  bgp {br2.get('bgp_session',0):.4f}")
    print()
    print("  Production wiring:")
    print("  • Replace generate_normal_snapshots() with Spanner NetworkMetrics query")
    print("  • Replace EDGE_INDEX_DICT with query to PhysicalLink / BGPSession nodes")
    print("  • Write branch anomaly scores back to NodeEmbedding table in Spanner")
    print()


if __name__ == "__main__":
    torch.manual_seed(42)
    main()
