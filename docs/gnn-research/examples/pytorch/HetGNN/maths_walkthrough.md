# HetGNN Maths Walkthrough — PyTorch / torch_geometric

Every arithmetic step shown with concrete numbers.

Code reference: `simple_hetgnn_pinpointing.py`  →  `HetGNNAutoencoder`  
Production reference: `gnn/src/model/hetgnn.py`

---

## Setup

```
  [router: pe1] ──has_interface──▶ [interface: pe1_eth1]
  [router: p1 ] ──has_interface──▶ [interface: p1_eth3 ]
                                         ↕ connects_to
  [router: pe1] ──has_bgp──────▶  [bgp_session: pe1_bgp]
```

**Node counts and feature dims (F=3 each, H=2 for this walkthrough):**

| Type | N | Nodes | Features |
|------|---|-------|---------|
| router | 2 | pe1, p1 | cpu, mem, ospf_state |
| interface | 2 | pe1_eth1, p1_eth3 | tx_drops, rx_drops, mtu_norm |
| bgp_session | 1 | pe1_bgp | bgp_state, pfx_count, uptime |

**Healthy features:**
```
X_R = [[0.22, 0.30, 1.0],   # pe1
        [0.20, 0.30, 1.0]]   # p1

X_I = [[0.01, 0.01, 0.167],  # pe1_eth1
        [0.01, 0.01, 0.167]]  # p1_eth3

X_B = [[1.0,  0.50, 0.80]]   # pe1_bgp
```

---

## Why typed projections? (vs shared GCN weights)

A homogeneous GCN assumes all nodes speak the same "language" — the same F features with
the same meaning. That works if all nodes are routers. Here the graph has three node types
with completely different feature semantics:

- `cpu_percent` (router) is a fractional utilisation score
- `tx_drops_rate` (interface) is a log-scaled counter
- `bgp_state` (bgp_session) is a binary UP/DOWN flag

If we forced these into one shared feature vector and one shared weight matrix, the model
would try to interpret BGP state as if it were a CPU reading — which produces nonsense.
More subtly, it would conflate interface drops with router memory, making it impossible to
identify *which layer* a fault is in even if it detects that *something* is wrong.

In a GCN every node has the same F features and shares one weight matrix W [F, H].
Here, routers have `cpu/mem/ospf`, interfaces have `drops/mtu`, BGP has `state/prefixes`.
These features are **semantically incompatible** — averaging them or sharing one W would
be nonsensical.

**Solution:** `lin_dict` in `HetGNNAutoencoder` gives each type its own:
```python
self.lin_dict['router']      = nn.Linear(3, H)   # cpu,mem,ospf   → latent
self.lin_dict['interface']   = nn.Linear(3, H)   # drops,mtu      → latent
self.lin_dict['bgp_session'] = nn.Linear(3, H)   # state,pfx,upt  → latent
```

All three project into the **same** latent space (dim H), so message passing
can then mix them meaningfully.

---

## Part A — Typed Linear Projections  (`lin_dict[nt](x).relu()`)

**W_proj_R [3→2], W_proj_I [3→2], W_proj_B [3→2]** (trained weights — example values):

```
W_proj_R = [[ 0.5,  0.2 ],   W_proj_I = [[ 0.8,  0.1 ],   W_proj_B = [[ 0.6,  0.3 ],
             [ 0.3,  0.4 ],               [ 0.2,  0.6 ],               [ 0.2,  0.5 ],
             [ 0.1,  0.3 ]]               [ 0.4,  0.7 ]]               [ 0.1,  0.4 ]]
```

### A.1 — Router projections

**pe1 = [0.22, 0.30, 1.0]:**
```
h0: 0.22×0.5 + 0.30×0.3 + 1.0×0.1  =  0.110 + 0.090 + 0.100  =  0.300
h1: 0.22×0.2 + 0.30×0.4 + 1.0×0.3  =  0.044 + 0.120 + 0.300  =  0.464

h_R[pe1] = ReLU([0.300, 0.464]) = [0.300, 0.464]
```

**p1 = [0.20, 0.30, 1.0]:**
```
h0: 0.20×0.5 + 0.30×0.3 + 1.0×0.1  =  0.100 + 0.090 + 0.100  =  0.290
h1: 0.20×0.2 + 0.30×0.4 + 1.0×0.3  =  0.040 + 0.120 + 0.300  =  0.460

h_R[p1] = [0.290, 0.460]
```

### A.2 — Interface projections (healthy: tx_drops=0.01, rx=0.01, mtu=0.167)

**pe1_eth1 = p1_eth3 = [0.01, 0.01, 0.167]:**
```
h0: 0.01×0.8 + 0.01×0.2 + 0.167×0.4  =  0.008 + 0.002 + 0.067  =  0.077
h1: 0.01×0.1 + 0.01×0.6 + 0.167×0.7  =  0.001 + 0.006 + 0.117  =  0.124

h_I[pe1_eth1] = h_I[p1_eth3] = [0.077, 0.124]
```

### A.3 — BGP projection (healthy: bgp=1.0, pfx=0.50, upt=0.80)

**pe1_bgp = [1.0, 0.50, 0.80]:**
```
h0: 1.0×0.6 + 0.50×0.2 + 0.80×0.1  =  0.600 + 0.100 + 0.080  =  0.780
h1: 1.0×0.3 + 0.50×0.5 + 0.80×0.4  =  0.300 + 0.250 + 0.320  =  0.870

h_B[pe1_bgp] = [0.780, 0.870]
```

---

## Part B — Heterogeneous Message Passing  (`HeteroConv(SAGEConv, aggr='sum')`)

**What message passing achieves in a heterogeneous graph:** After the typed projections
all nodes live in the same H-dimensional latent space. Now we let them talk. The key
difference from a homogeneous GCN is that the *meaning* of a message depends on what
type of entity sent it. A message from a router to an interface carries information about
the router's CPU and OSPF state; a message from one interface to another carries
information about the connected link's drop rate. Using separate `SAGEConv` modules per
edge type means the model learns separate transformation weights for each type of
relationship — it "listens differently" to a router update vs a link-peer update.

After message passing, each interface node's embedding encodes not just its own drops and
MTU but also what the router above it and the interface across the link are currently doing.
This is what lets the model distinguish "this interface is dropping packets because of a
local MTU misconfiguration" from "this interface looks bad because the router above it is
overloaded" — the fault signal comes from different directions.

### How SAGEConv differs from GCN

**GCN:**
```
h_v^new = ReLU( Â_norm_row_v @ H_all @ W )
```
One shared W, one aggregation over all neighbours (normalised sum).

**SAGEConv (GraphSAGE, used in production):**
```
h_v^new = ReLU( W_self @ h_v  +  W_neigh @ (1/|N(v)|) Σ_{u∈N(v)} h_u )
```
Two separate weight matrices — one for self, one for neighbours.
In PyG's heterogeneous mode, for edge type `(src_type, rel, dst_type)`:
```
h_dst^new = W_root @ h_dst  +  W_neigh @ mean_{u ∈ N_src(dst)} h_src[u]
```

**`HeteroConv` routes messages by edge type** — each SAGEConv only fires for its
assigned edge type, with its own private `W_root` and `W_neigh`.

The two-weight design of SAGEConv also fixes a subtle problem with plain GCN in
heterogeneous graphs: the GCN aggregation mixes self and neighbours into one weighted sum,
so in a bipartite edge (router→interface) the destination node (interface) has no "self"
contribution in the usual sense — it only receives from routers. SAGEConv handles bipartite
edges correctly by keeping `W_root` (applied to the destination's own embedding) and
`W_neigh` (applied to the source neighbours) as separate linear maps, then summing them.

### B.1 — Edge type: ('router', 'has_interface', 'interface')

Edge index: pe1(router 0)→pe1_eth1(iface 0), p1(router 1)→p1_eth3(iface 1)

Weights for this conv (W_root_RI [2,2], W_neigh_RI [2,2]):
```
W_root_RI  = [[ 0.6,  0.1 ],    W_neigh_RI = [[ 0.3,  0.4 ],
               [ 0.2,  0.7 ]]                  [ 0.5,  0.2 ]]
```

**Update pe1_eth1** (destination = interface 0, source = router 0 = pe1):
```
self_part = h_I[pe1_eth1] @ W_root_RI:
  s0: 0.077×0.6 + 0.124×0.2  =  0.046 + 0.025  =  0.071
  s1: 0.077×0.1 + 0.124×0.7  =  0.008 + 0.087  =  0.095

neigh_part = h_R[pe1] @ W_neigh_RI:   (1 neighbour, mean = itself)
  n0: 0.300×0.3 + 0.464×0.5  =  0.090 + 0.232  =  0.322
  n1: 0.300×0.4 + 0.464×0.2  =  0.120 + 0.093  =  0.213

msg_RI[pe1_eth1] = [0.071+0.322,  0.095+0.213] = [0.393, 0.308]
```

**Update p1_eth3** (mirror — p1 router feeds p1_eth3):
```
self_part = h_I[p1_eth3] @ W_root_RI = [0.071, 0.095]   (same features)
neigh_part = h_R[p1] @ W_neigh_RI:
  n0: 0.290×0.3 + 0.460×0.5  =  0.087 + 0.230  =  0.317
  n1: 0.290×0.4 + 0.460×0.2  =  0.116 + 0.092  =  0.208

msg_RI[p1_eth3] = [0.071+0.317,  0.095+0.208] = [0.388, 0.303]
```

### B.2 — Edge type: ('interface', 'connects_to', 'interface')

Both interfaces share the same features → same embeddings → symmetric peer messages.

Weights for this conv (W_root_II [2,2], W_neigh_II [2,2]):
```
W_root_II  = [[ 0.5,  0.2 ],    W_neigh_II = [[ 0.4,  0.1 ],
               [ 0.3,  0.6 ]]                  [ 0.2,  0.5 ]]
```

**Update pe1_eth1** (receives from p1_eth3):
```
self_part = h_I[pe1_eth1] @ W_root_II:
  s0: 0.077×0.5 + 0.124×0.3  =  0.039 + 0.037  =  0.076
  s1: 0.077×0.2 + 0.124×0.6  =  0.015 + 0.074  =  0.090

neigh_part = h_I[p1_eth3] @ W_neigh_II:   (peer = p1_eth3)
  n0: 0.077×0.4 + 0.124×0.2  =  0.031 + 0.025  =  0.056
  n1: 0.077×0.1 + 0.124×0.5  =  0.008 + 0.062  =  0.070

msg_II[pe1_eth1] = [0.076+0.056,  0.090+0.070] = [0.132, 0.160]
```

### B.3 — HeteroConv aggregation (aggr='sum')

**Why `aggr='sum'` instead of `'mean'`?** With `'mean'`, each message is divided by the
number of contributing edge types. For a node like pe1_eth1 that receives from two
different edge types (one `has_interface` message and one `connects_to` message), `'mean'`
would halve the contribution of each. `'sum'` preserves the full magnitude of each message,
letting the model freely learn how to weight the different signal sources through its weight
matrices. In practice `'mean'` is safer when degree varies widely; `'sum'` is preferred
here because the number of edge types per node is fixed by the schema.

For destination pe1_eth1 (interface type), HeteroConv sums contributions
from all edge types that write to the `interface` destination:

```
h_I_new[pe1_eth1] = ReLU( msg_RI[pe1_eth1] + msg_II[pe1_eth1] )
                  = ReLU( [0.393, 0.308] + [0.132, 0.160] )
                  = ReLU( [0.525, 0.468] )
                  = [0.525, 0.468]
```

### B.4 — BGP update: edge type ('router', 'has_bgp', 'bgp_session')

**Why does router state flow into the BGP embedding?** The `has_bgp` edge exists because
a BGP session runs *on* a router — if the router's CPU is overloaded, its BGP process may
miss keepalives and flap the session. By having the router send a message to its BGP node,
the model captures this upstream dependency. In a fault scenario where the router is healthy
but the BGP session tears down independently, the router message provides a "control" signal
that distinguishes "BGP down due to router overload" from "BGP down due to an external
policy change" — the former would show elevated router features in the message; the latter
would not.

pe1 → pe1_bgp (1 edge, 1 neighbour). Similar calculation:

```
msg_RB[pe1_bgp]:
  self_part  = h_B[pe1_bgp] @ W_root_RB
  neigh_part = h_R[pe1]     @ W_neigh_RB
  ↓
h_B_new[pe1_bgp] = ReLU(self + neigh)   ← router health propagates into BGP embedding
```

BGP has no `connects_to` type edges, so only one source of messages.

---

## Part C — Decoder and Branch Anomaly Scores

### C.1 — Per-type decode (`decoder_dict[nt](h)`)

**Why a separate decoder per branch?** This is the mechanism that enables branch-level
diagnosis. If all types shared one decoder, a large reconstruction error on the BGP features
could leak into the interface reconstruction — the gradients would interfere and the model
could not separately attribute errors to different fault layers.

With per-type decoders, the interface decoder only ever sees interface embeddings and only
ever outputs interface features. Its reconstruction error is a pure measure of how much the
interface branch deviated from healthy. Whichever branch's decoder reports the highest error
is the answer to "which layer is the fault in?" — configuration, protocol, or physical.

```python
self.decoder_dict['router']      = nn.Linear(H=2, F=3)
self.decoder_dict['interface']   = nn.Linear(H=2, F=3)
self.decoder_dict['bgp_session'] = nn.Linear(H=2, F=3)
```

Each branch reconstructs its own features independently from its own embedding.
Trained on healthy data: recon ≈ actual for healthy nodes.

### C.2 — Fault 1: MTU mismatch on pe1_eth1

Fault snapshot:
```
X_I_fault = [[0.75, 0.01, 0.156],   # pe1_eth1: tx_drops spike, mtu 1400
              [0.01, 0.01, 0.167]]   # p1_eth3: healthy
```

Projection of pe1_eth1 fault:
```
h_I_fault[pe1_eth1]:
  h0: 0.75×0.8 + 0.01×0.2 + 0.156×0.4  =  0.600 + 0.002 + 0.062  =  0.664  (was 0.077)
  h1: 0.75×0.1 + 0.01×0.6 + 0.156×0.7  =  0.075 + 0.006 + 0.109  =  0.190  (was 0.124)

h_I_fault[pe1_eth1] = [0.664, 0.190]   vs healthy [0.077, 0.124]
```

After message passing, the interface embedding for pe1_eth1 is displaced far
from its trained healthy cluster. The decoder cannot reconstruct correctly:

```
recon_I[pe1_eth1] ≈ [0.08, 0.02, 0.170]   (decoder expects healthy features)
actual_I[pe1_eth1] = [0.75, 0.01, 0.156]

MSE:
  tx_drops: (0.75 - 0.08)² = 0.449
  rx_drops: (0.01 - 0.02)² = 0.000
  mtu_norm: (0.156 - 0.170)² = 0.000

MSE_interface = (0.449 + 0.000 + 0.000) / 3 = 0.150
```

The router and BGP embeddings are unchanged (their inputs didn't change):
```
MSE_router      ≈ 0.000
MSE_bgp_session ≈ 0.000
```

### C.3 — Branch scores

```
branch_score[nt] = max over nodes of: mean((recon[nt] - x[nt])², dim=features)
```

```
  router      0.000   ✓ normal
  interface   0.150   ⚠️  FAULT LAYER  ← tx_drops drove the embedding out of range
  bgp_session 0.000   ✓ normal

→ Root layer: interface
```

### C.4 — Fault 2: BGP session teardown

```
X_B_fault = [[0.0, 0.0, 0.0]]   # pe1_bgp: DOWN, 0 prefixes, 0 uptime
```

BGP projection of fault:
```
h_B_fault[pe1_bgp]:
  h0: 0.0×0.6 + 0.0×0.2 + 0.0×0.1  =  0.000   (was 0.780)
  h1: 0.0×0.3 + 0.0×0.5 + 0.0×0.4  =  0.000   (was 0.870)

h_B_fault = [0.000, 0.000]  ← completely zeroed
```

Decoder tries to reconstruct from zero embedding → wrong predictions:
```
recon_B[pe1_bgp] ≈ [0.50, 0.25, 0.40]   (decoder bias towards average)
actual_B[pe1_bgp] = [0.0, 0.0, 0.0]

MSE:
  bgp_state:     (0.0 - 0.50)² = 0.250
  pfx_count:     (0.0 - 0.25)² = 0.063
  uptime_norm:   (0.0 - 0.40)² = 0.160

MSE_bgp = (0.250 + 0.063 + 0.160) / 3 = 0.158
```

Branch scores:
```
  router      0.000   ✓ normal
  interface   0.000   ✓ normal
  bgp_session 0.158   ⚠️  FAULT LAYER

→ Root layer: bgp_session
```

---

## Part D — Multi-task Training Loss

**Multi-task learning — what it means and why it helps:** Training three branches at once
with a shared backbone is called multi-task learning. The `HeteroConv` message-passing
layers are shared — their weights are updated by the combined gradient of all three branch
losses simultaneously. This is beneficial because the shared layers learn representations
that are simultaneously useful for reconstructing router state, interface state, and BGP
state. A feature that is irrelevant to all three branches will receive small gradient
updates from every direction and will be suppressed; a feature that matters for even one
branch will be preserved.

**Gradient isolation by branch:** However, `W_proj_I` (the interface input projection) and
`W_dec_I` (the interface decoder) are updated *only* by `loss_interface`. If a BGP fault
causes `loss_bgp` to spike, those gradients flow only through `W_proj_B` and `W_dec_B` and
do not touch any interface weights. This ensures that the interface decoder stays calibrated
to healthy interface states even when BGP faults are frequent — and vice versa.

```python
loss = (loss_router + loss_interface + loss_bgp) / 3.0
```

Each branch independently learns to reconstruct its own features. The gradients
for the interface branch cannot corrupt the BGP branch — they are separated by
independent `W_proj` and `W_dec` matrices.

This is the key advantage over GCN: when the interface branch fires, we know
the fault is in the physical/config layer, not in a protocol or hardware layer.

---

## Summary of every formula

| Step | Formula | PyTorch code |
|------|---------|-------------|
| Typed projection | h_nt = ReLU(x_nt @ W_proj_nt) | `self.lin_dict[nt](x).relu()` |
| SAGEConv | h_dst = W_root@h_dst + W_neigh@mean(h_src) | `SAGEConv((-1,-1), H)` inside HeteroConv |
| Edge routing | HeteroConv dispatches by (src_type, rel, dst_type) | `HeteroConv(conv_dict, aggr='sum')` |
| Decode | recon_nt = h_nt @ W_dec_nt + b_nt | `self.decoder_dict[nt](h)` |
| Node score | score_i = mean((recon_i - x_i)²) | `((recon-x)**2).mean(dim=1)` |
| Branch score | branch = max(node_scores_nt) | `float(err.max())` |
| Train loss | L = (L_R + L_I + L_B) / 3 | `(loss_r + loss_i + loss_b) / 3.0` |
