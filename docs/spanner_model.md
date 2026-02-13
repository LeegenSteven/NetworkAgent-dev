# Spanner Network Graph Data Model

This document explains the data model used in Cloud Spanner to represent the network topology and its history. It covers the **SCD Type 2** table design, the **Property Graph** definitions, and how to query the data using **SQL** and **GQL**.

## 1. Core Data Model: SCD Type 2

The database tracks the history of every network entity using **Slowly Changing Dimensions (SCD) Type 2**. This means we don't just store the *current* state of a router or interface; we store every version of it that has ever existed, defined by a validity time window.

### Schema Pattern
Every topological table (`PhysicalRouter`, `PhysicalInterface`, `PhysicalLink`, etc.) has these two timestamp columns:
- `valid_start_ts`: When this version of the entity became active.
- `valid_end_ts`: When this version was replaced or deleted. (NULL means it is currently active).

**Primary Key**: `(id, valid_start_ts DESC)`

### Active Row Logic
To find the state of an entity at any given time `T`, you must filter for rows where `T` falls within the validity window:

```sql
valid_start_ts <= @T AND (valid_end_ts > @T OR valid_end_ts IS NULL)
```

## 2. Data Dictionary & Relationships

### Physical Layer
These tables represent the physical infrastructure of the network.

| Table | Description | Key Relationships |
| :--- | :--- | :--- |
| `PhysicalRouter` | Represents a physical router device. | **Parent** of `PhysicalInterface`. Hosted `VRF`s located here. |
| `PhysicalInterface` | specialized hardware interface on a router. | **Child** of `PhysicalRouter`. Connects to `PhysicalLink`. Associated with `LogicalSubnet`. |
| `PhysicalLink` | Cable or fiber connecting two interfaces. | Connects two `PhysicalInterface`s. |
| `LogicalSubnet` | IPv4/IPv6 subnet defined on an interface. | Associated with `PhysicalInterface`. |
| `Interface_Link` | **Edge Table (Temporal)**: Resolves many-to-many between Interface and Link. | Joins `PhysicalInterface` and `PhysicalLink`. |
| `Subnet_Association` | **Edge Table (Temporal)**: Maps Subnets to Interfaces or other entities. | Joins `PhysicalInterface` and `LogicalSubnet`. |

### Logical Layer
These tables represent the virtualized network services (L3VPN, BGP).

| Table | Description | Key Relationships |
| :--- | :--- | :--- |
| `Customer` | The entity owning the VPN service. | Owns `L3VPNService`. |
| `L3VPNService` | A logical VPN instance (e.g., "Finance VPN"). | Owned by `Customer`. Contains `VRF`s. |
| `VRF` | Virtual Routing and Forwarding instance on a router. | Belongs to `L3VPNService`. Located on `PhysicalRouter`. Contains `BGPSession`s. |
| `BGPSession` | A BGP neighbor configuration. | Belongs to `VRF`. Peers with another `BGPSession`. |
| `BGP_Peering` | **Edge Table (Temporal)**: Represents an active BGP session state between two neighbors. | Joins two `BGPSession`s. |

### Observability & Metrics
Tables tracking events, metrics, and performance data.

| Table | Description | Key Relationships |
| :--- | :--- | :--- |
| `NetworkMetrics` | Time-series metrics (throughput, error rates). | Linked to `PhysicalInterface`. |
| `ServicePerformance` | End-to-end service latency/availability. | Linked to `L3VPNService`. |
| `Incident` | Operational incidents/alerts. | (Self-contained) |
| `AgentTrace` | Tracing data for the Operator/Agent actions. | (Self-contained) |

## 3. Property Graph Definition (`networkGraph`)

The Spanner Graph feature layers a property graph model over these tables. The graph is defined in the schema with the `CREATE PROPERTY GRAPH` statement.

### Nodes
Nodes map directly to the underlying tables.
- `PhysicalRouter`
- `PhysicalInterface`
- `PhysicalLink`
- `L3VPNService`
- `VRF`
- `BGPSession`
- ... and more.

### Edges
Edges are defined as **Views** that dynamically join the underlying tables while respecting the SCD Type 2 constraints.

| Edge Name | Source | Target | Description |
| :--- | :--- | :--- | :--- |
| `HasInterface` | `PhysicalRouter` | `PhysicalInterface` | Router owns Interface |
| `ConnectsTo` | `PhysicalInterface` | `PhysicalLink` | Interface connects to Link |
| `LinkedTo` | `PhysicalLink` | `PhysicalInterface` | Link connects to Interface |
| `LocatedOn` | `VRF` | `PhysicalRouter` | VRF is configured on Router |
| `RealizesVPN` | `VRF` | `L3VPNService` | VRF belongs to VPN Service |
| `PeersWith` | `BGPSession` | `BGPSession` | BGP Session logical peering |

## 3. Querying the Data

You can query the data using standard GoogleSQL or the Graph Query Language (GQL).

### SQL: Fetching Topology at Time T

To reconstruct the graph manually (e.g., for ML training pipelines), you select from tables implementing the time-slice logic.

**Example: Get all active Routers and their Interfaces at 2023-10-27 10:00:00 UTC**

```sql
-- Parameters: @ts = TIMESTAMP('2023-10-27 10:00:00')

SELECT 
  r.name AS router_name,
  i.name AS interface_name
FROM PhysicalRouter r
JOIN PhysicalInterface i ON r.id = i.router_id
WHERE 
  -- Router is valid at T
  r.valid_start_ts <= @ts AND (r.valid_end_ts > @ts OR r.valid_end_ts IS NULL)
  -- Interface is valid at T
  AND i.valid_start_ts <= @ts AND (i.valid_end_ts > @ts OR i.valid_end_ts IS NULL)
```

### GQL: Graph Traversal

GQL allows for more expressive path traversals. Since the graph is defined over SCD Type 2 tables, you must ensure you are matching consistent versions of nodes and edges.

*Note: As of early 2025, Spanner Graph GQL support for temporal views is evolving. Check the latest documentation for `FOR SYSTEM_TIME AS OF` support.*

**Example: Find all Interfaces connected to a Router**

```sql
GRAPH networkGraph
MATCH (r:PhysicalRouter)-[e:HasInterface]->(i:PhysicalInterface)
WHERE r.name = "router-1"
  -- Filter for current version (simplify if just wanting latest)
  AND r.valid_end_ts IS NULL 
  AND i.valid_end_ts IS NULL
RETURN i.name, i.speed
```

**Example: Find End-to-End Path (Router -> Interface -> Link -> Interface -> Router)**

```sql
GRAPH networkGraph
MATCH (src:PhysicalRouter)-[:HasInterface]->(i1)-[:ConnectsTo]->(l:PhysicalLink)-[:LinkedTo]->(i2)<-[:HasInterface]-(dst:PhysicalRouter)
WHERE src.name = "edge-router-a"
RETURN dst.name AS connected_router, l.bandwidth
```

## 4. Derived Edges without Foreign Keys

In our schema, we removed rigid Foreign Key constraints to increase write throughput and flexibility for the SCD model. Relationships are maintained via:
1.  **Logical IDs**: `router_id` column in `PhysicalInterface` table contains the ID of the router.
2.  **Edge Views**: The `CREATE VIEW ... AS SELECT ... JOIN ...` statements in `spanner.j2` explicitly define how these logical IDs resolve to edges, handling the timestamp logic automatically.

When you query the **Graph** (GQL), these Views are used, so you don't need to manually write the complex time-window JOINs every time.
