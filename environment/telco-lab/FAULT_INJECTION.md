# L3VPN Fault Injection for GNN Root Cause Analysis

This document describes the fault injection strategy for the L3VPN hub-spoke network to demonstrate GNN embedding-based root cause analysis.

## Overview

The L3VPN hub-spoke network provides three fault injection variants that introduce misconfigurations in the VyOS router configurations. These faults are designed to be detectable through Graph Neural Network (GNN) embeddings stored in Spanner, allowing you to trace the root cause of network issues using graph-based analysis.

## Network Architecture

### Hub-Spoke Topology
- **Hub Router**: PE2 (Cambridge) - Aggregates traffic from all spokes
- **Spoke Routers**: 
  - PE1 (Oxford) - Connected to CE1-spoke (Sheffield)
  - PE3 (Brighton) - Connected to CE2-spoke (Liverpool)

### Route Target Configuration (Correct)
- **Spokes (PE1, PE3)**: 
  - Export: `65035:1011` (spoke routes)
  - Import: `65035:1030` (hub routes)
- **Hub (PE2)**:
  - Export: `65035:1030` (hub routes)
  - Import: `65035:1011`, `65035:1030` (both spoke and hub routes)

### Route Distinguishers (Correct)
- **PE1**: `10.50.50.1:1011`
- **PE2**: `10.80.80.1:1011`
- **PE3**: `10.60.60.1:1011`

## Fault Variants

### Fault 1: RT Import Misconfiguration (Severe)

**File**: `l3vpn-hub-spoke-fault1-rt-import.yaml`

**Misconfiguration**:
- **Location**: PE1 router, BLUE_SPOKE VRF
- **Change**: `rt_import` changed from `["65035:1030"]` to `["65035:9999"]`
- **Line**: ~970 in the VyOSL3VPN section

**Impact**:
- **Severity**: Complete connectivity failure between spoke1 and hub
- **Symptom**: PE1 cannot import routes from the hub (PE2)
- **Affected Traffic**: 
  - dev1 (10.100.1.10) ❌ devhub (10.100.2.10)
  - dev1 ✅ dev2 (via hub) - works if hub can still reach dev1's routes
- **BGP Behavior**: PE1 will reject all VPNv4 routes with RT `65035:1030`

**GNN Detection Signature**:
- **Router Embedding**: PE1 config embedding shows high reconstruction error
- **Feature Attribution**: Config (semantic) feature is primary anomaly driver
- **Temporal Signal**: Embedding diverges at fault injection time
- **Interface Metrics**: pe1-eth2 shows dropped traffic/zero throughput
- **Graph Propagation**: Anomaly localizes to PE1 node and connected interfaces

### Fault 2: RT Export Misconfiguration (Moderate)

**File**: `l3vpn-hub-spoke-fault2-rt-export.yaml`

**Misconfiguration**:
- **Location**: PE1 router, BLUE_SPOKE VRF
- **Change**: `rt_export` changed from `["65035:1011"]` to `["65035:8888"]`
- **Line**: ~960 in the VyOSL3VPN section

**Impact**:
- **Severity**: Asymmetric connectivity failure
- **Symptom**: Hub cannot receive routes from PE1
- **Affected Traffic**:
  - devhub (10.100.2.10) ❌ dev1 (10.100.1.10) - fails
  - dev1 → devhub - may work (PE1 can import hub routes)
  - dev2 ❌ dev1 (via hub) - fails in one direction
- **BGP Behavior**: PE1 exports routes with wrong RT that hub doesn't import

**GNN Detection Signature**:
- **Router Embedding**: PE1 and PE2 both show config anomalies
- **Feature Attribution**: Config semantic embeddings differ from baseline
- **Temporal Signal**: Both routers' embeddings shift at injection time
- **Interface Metrics**: Asymmetric traffic patterns on pe1-eth2
- **Graph Propagation**: Anomaly visible on both PE1 and PE2, but originated at PE1

### Fault 3: RD Conflict (Subtle)

**File**: `l3vpn-hub-spoke-fault3-rd-conflict.yaml`

**Misconfiguration**:
- **Location**: PE2 router (hub), BLUE_HUB VRF
- **Change**: `rd` changed from `"10.80.80.1:1011"` to `"10.50.50.1:1011"` (duplicates PE1's RD)
- **Line**: ~1000 in the VyOSL3VPN section

**Impact**:
- **Severity**: Intermittent, unpredictable behavior
- **Symptom**: Route shadowing - BGP cannot distinguish between routes from PE1 and PE2
- **Affected Traffic**: All flows, with unpredictable routing decisions
- **BGP Behavior**: 
  - Routes from PE1 and PE2 appear identical (same RD)
  - BGP best path selection becomes non-deterministic
  - May cause traffic loops or blackholing

**GNN Detection Signature**:
- **Router Embedding**: Both PE1 and PE2 show config anomalies
- **Feature Attribution**: Config semantic features on both routers
- **Temporal Signal**: Synchronized embedding changes on multiple routers
- **Interface Metrics**: Erratic traffic patterns, potential packet loss
- **Graph Propagation**: Multi-node anomaly with correlated changes

## How to Use

### 1. Deploy the Normal (Baseline) Network

```bash
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml
```

Wait for the network to stabilize and collect baseline metrics.

### 2. Generate Baseline Embeddings

Trigger GNN inference to capture the healthy state:

```bash
curl -X POST http://<gnn-serve-endpoint>:8080/inference
```

This stores embeddings in Spanner's `NodeEmbedding` table.

### 3. Inject a Fault

Choose one of the fault variants and deploy it:

```bash
# Fault 1: RT Import Misconfiguration
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault1-rt-import.yaml

# OR Fault 2: RT Export Misconfiguration  
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault2-rt-export.yaml

# OR Fault 3: RD Conflict
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault3-rd-conflict.yaml
```

The operator will:
1. Detect the configuration change
2. Regenerate VyOSRouter CRs with the faulty config
3. Reconfigure the affected VyOS router(s)
4. Update Spanner with the new configuration

### 4. Generate Post-Fault Embeddings

After the fault is injected and traffic is affected:

```bash
curl -X POST http://<gnn-serve-endpoint>:8080/inference
```

### 5. Analyze Embeddings for Root Cause

#### Query 1: Find Routers with Anomalous Embeddings

```sql
SELECT 
    node_id,
    node_type,
    anomaly_score,
    anomaly_explanation,
    timestamp
FROM NodeEmbedding
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
  AND anomaly_score > 0.1
ORDER BY anomaly_score DESC
```

#### Query 2: Compare Embeddings Before/After Fault

```sql
WITH baseline AS (
    SELECT node_id, embedding
    FROM NodeEmbedding
    WHERE timestamp < TIMESTAMP('<fault_injection_time>')
      AND node_id = 'pe1'
    ORDER BY timestamp DESC
    LIMIT 1
),
faulty AS (
    SELECT node_id, embedding
    FROM NodeEmbedding  
    WHERE timestamp > TIMESTAMP('<fault_injection_time>')
      AND node_id = 'pe1'
    ORDER BY timestamp ASC
    LIMIT 1
)
SELECT 
    b.node_id,
    b.embedding AS baseline_embedding,
    f.embedding AS faulty_embedding
FROM baseline b
JOIN faulty f ON b.node_id = f.node_id
```

#### Query 3: Use Graph Traversal for Root Cause

```gql
GRAPH networkGraph
MATCH (router:NetworkNode {kind: 'PhysicalRouter'}) 
      -[:RouterHasEmbedding]-> 
      (emb:NetworkNode {kind: 'NodeEmbedding'})
WHERE emb.anomaly_score > 0.1
  AND emb.timestamp >= TIMESTAMP('<fault_injection_time>')
RETURN 
    router.name AS router_name,
    router.role AS router_role,
    emb.anomaly_score AS score,
    emb.anomaly_explanation AS explanation
ORDER BY emb.anomaly_score DESC
```

#### Query 4: Trace Affected Interfaces

```gql
GRAPH networkGraph
MATCH (router:NetworkNode {kind: 'PhysicalRouter', name: 'pe1'})
      -[:HasInterface]->
      (intf:NetworkNode {kind: 'PhysicalInterface'})
      -[:InterfaceHasEmbedding]->
      (emb:NetworkNode {kind: 'NodeEmbedding'})
WHERE emb.timestamp >= TIMESTAMP('<fault_injection_time>')
RETURN 
    intf.name AS interface_name,
    emb.anomaly_score AS score,
    emb.anomaly_explanation AS explanation
```

### 6. Restore Normal Configuration

```bash
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml
```

## Expected Results

### Fault 1 (RT Import)
- **Anomaly Score**: High (>0.5) on PE1
- **Primary Feature**: Config semantic embedding
- **Affected Nodes**: PE1 router, pe1-eth2 interface
- **Traffic Pattern**: Zero traffic on pe1-eth2 to CE

### Fault 2 (RT Export)  
- **Anomaly Score**: Moderate (0.3-0.5) on PE1, Low (0.1-0.3) on PE2
- **Primary Feature**: Config semantic embedding on both
- **Affected Nodes**: PE1 (primary), PE2 (secondary)
- **Traffic Pattern**: Asymmetric traffic (one-way failure)

### Fault 3 (RD Conflict)
- **Anomaly Score**: Moderate (0.2-0.4) on PE1 and PE2
- **Primary Feature**: Config semantic embedding
- **Affected Nodes**: PE1 and PE2 (correlated)
- **Traffic Pattern**: Erratic, intermittent packet loss

## Troubleshooting

### Operator Not Reconfiguring Routers

Check the operator logs:
```bash
kubectl logs -n <namespace> -l app=network-operator -f
```

Verify VyOSRouter CRs were updated:
```bash
kubectl get vyosrouter pe1 -o yaml | grep -A 20 "vrfs:"
```

### GNN Inference Failing

Check the serve logs:
```bash
kubectl logs -n <namespace> -l app=gnn-serve -f
```

Verify Spanner connectivity and model artifacts are loaded.

### No Embeddings in Spanner

Verify the `/inference` endpoint is accessible:
```bash
curl -v http://<gnn-serve-endpoint>:8080/health
```

Check that the GNN model has been trained and artifacts exist in GCS.

## Integration with Traffic Tests

You can run traffic tests to validate the fault impact:

```bash
kubectl apply -f environment/telco-lab/l3vpn-test.yaml
```

This will generate traffic between devices and populate NetworkMetrics in Spanner, which feed into the GNN model as interface features (rx, tx, errors).

## References

- **GNN Architecture**: `docs/embedding.md`
- **Spanner Schema**: `environment/spanner.j2`
- **GNN Training Pipeline**: `gnn/src/train/pipeline.py`
- **GNN Inference Service**: `gnn/src/serve.py`
- **L3VPN Documentation**: `docs/l3vpn.md`
