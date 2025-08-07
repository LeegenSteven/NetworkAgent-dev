# Network Services

The demo manages an end to end 5G virtual mobile network, The 5G network service topology shown below can be automated into an operational state within a single GCP project.

![virtual mobile network](/drawings/free5gc/mobile.drawio.svg)

The following virtual infrastructure can be deployed to instantiate the 5G network service. 

* __Core Network Site:__ Based on [Free5gc 5G Core network functions](https://free5gc.org/)
    * GCP networks are deployed to attach DNN mimicking the Internet and a Core network attaches control plane and upf network functions
    * Free5gc Control Plane CNFs are deployed in a virtual machine attached to the core network. 
    * Free5gc UPF VNF is deployed attached to core and internet networks, routing between the two. 
* __Radio Sites:__ [Running Radio simulators](https://github.com/aligungr/UERANSIM)
    * GCP network per radio "site"
    * UERANSIM gNB Radio Network Simulator VNF is attached to the cellsite network. UE simulators can establish sessions and test traffic routed through the 5G network to the internet services above. 
* __Transport Services:__ [Connecting all sites](/docs/connectivityservices.md)
    * Wireguard VNFs create a set of tunnels in a mesh or point to point configuration between GCP networks.

Once deployed test traffic can be run from the simulated UEs across the network to the Internet. 

## Custom Resource Definitions (CRD)

The system models the network components as Kubernetes Custom Resource Definitions. Each components lifecycle is then implemented by an operator. Another operator updates the Network Topology with the changes made. 

### CRD Model Overview

The CRD model implements a hierarchical structure where higher-level services manage and orchestrate lower-level resources:

#### Connectivity Services
- **MeshService**: Top-level connectivity service that creates and manages multiple WireguardAppliance instances
- **PointToPointService**: Top-level connectivity service that creates and manages two WireguardAppliance instances

#### Network Services (Functions)
- **ControlPlane**: Top-level 5G service that orchestrates the core network functions
- **UserPlaneFunction**: Managed by ControlPlane, handles 5G traffic routing
- **DataNetwork**: Managed by ControlPlane, provides data network endpoints
- **UERanSIM**: Managed by ControlPlane, provides radio network simulation
- **WireguardAppliance**: Created and managed by MeshService or PointToPointService
- **UETest**: Test resources that reference and use UERanSIM and DataNetwork services

#### Cloud Infrastructure (Children)
- **ComputeInstance**: Created and managed by various services (ControlPlane, UERanSIM, etc.)
- **ComputeNetwork/ComputeSubnetwork**: Infrastructure resources managed by services

#### Hierarchy Examples

**5G Network Hierarchy:**
```
ControlPlane (parent)
└── ComputeInstance (child resource)

UserPlaneFunction (child service)
├── ComputeInstance (grandchild resource)
└── ComputeNetwork (grandchild resource)

DataNetwork (child service)
└── ComputeNetwork (grandchild resource)

UERanSIM (parent)
├── ComputeInstance (child resource)
└── ComputeSubnetwork (child resource)

UETest (parent)
├── References UERanSIM (dependency)
└── References DataNetwork (dependency)
```

**Connectivity Hierarchy:**
```
MeshService (parent)
├── WireguardAppliance-1 (child)
│   └── ComputeInstance (grandchild)
├── WireguardAppliance-2 (child)
│   └── ComputeInstance (grandchild)
└── WireguardAppliance-N (child)
    └── ComputeInstance (grandchild)

PointToPointService (parent)
├── WireguardAppliance-A (child)
│   └── ComputeInstance (grandchild)
└── WireguardAppliance-B (child)
    └── ComputeInstance (grandchild)
```

This hierarchy is automatically captured in the Spanner database through ResourceConnection entries, where parent UIDs reference child UIDs, enabling traversal of the complete service dependency tree.

## Network Topology

All the components of network services are modelled in the Spanner topology database. The database uses a graph-based model to represent network components and their relationships, enabling efficient querying and analysis of the network topology.

#### Core Tables

**NetworkNode**
- Primary table storing all network components and resources
- `id` (STRING): Unique identifier (UUID) for each network node
- `kind` (STRING): Type of resource (e.g., computeinstance, wireguardappliance, meshservice)
- `name` (STRING): Resource name
- `display_name` (STRING): Human-readable display name
- `self_link` (STRING): GCP resource self-link URL
- `status` (STRING): Current operational status
- `node_property` (JSON): Flexible storage for resource-specific properties and configurations

**ResourceConnection**
- Represents management/ownership relationships between resources
- `id` (STRING): Source node identifier
- `to_id` (STRING): Target node identifier (foreign key to NetworkNode)
- `connection_property` (JSON): Connection metadata and properties
- Used to model hierarchical relationships (e.g., VM instances managed by services)

**NetworkConnection**
- Represents network traffic connections between nodes
- `id` (STRING): Source node identifier
- `to_id` (STRING): Target node identifier
- `connection_property` (JSON): Network connection details (protocols, ports, etc.)
- Models actual network connectivity for traffic flow analysis

#### Knowledge Graph Tables

**KgResourceDescriptionNode**
- Stores semantic descriptions of network resources for AI/ML analysis
- `id` (STRING): Resource identifier
- `content` (STRING): Textual description of the resource
- `embedding` (ARRAY<FLOAT64>): Vector embeddings for semantic search

**KgLogEntryNode**
- Captures operational logs with semantic analysis capabilities
- `id` (STRING): Log entry identifier
- `severity` (STRING): Log severity level
- `source` (STRING): Log source system
- `message` (STRING): Log message content
- `timestamp` (TIMESTAMP): When the log was generated
- `content` (STRING): Full log content
- `embedding` (ARRAY<FLOAT64>): Vector embeddings for log analysis

#### Metrics Table

**NetworkMetrics**
- Time-series storage for network performance metrics
- `id` (STRING): Resource identifier
- `kind` (STRING): Metric type
- `name` (STRING): Metric name
- `timestamp` (INT64): Metric collection timestamp
- `metrics` (JSON): Metric values and metadata

#### Property Graph

The schema includes a property graph definition (`networkGraph`) that enables graph queries using GQL:
- **Nodes**: All entries from NetworkNode table
- **Edges**: 
  - `isConnectedTo`: Network connections representing traffic flow
  - `Manages`: Resource connections representing management relationships

This graph structure supports advanced network topology analysis, path finding, and relationship queries across the entire network infrastructure.

## CRD to Spanner Mapping

When CRDs are created, updated, or deleted, the operator translates these operations into corresponding Spanner database operations.

### Mapping Process

The operator's graph lifecycle code (`operator/src/graph/`) performs the following mappings:

#### 1. NetworkNode Creation
Each CRD instance becomes a NetworkNode entry:
- `id`: Kubernetes resource UID
- `kind`: CRD kind (e.g., "MeshService", "ControlPlane")
- `name`: Kubernetes resource name
- `display_name`: Human-readable format "{kind} ({name})"
- `status`: Extracted from resource status conditions or currentStatus
- `node_property`: Complete JSON serialization of the Kubernetes resource

#### 2. ResourceConnection Creation
Management relationships are created via ResourceConnection entries:
- Parent-child relationships based on Kubernetes ownerReferences
- Example: MeshService manages multiple WireguardAppliance instances
- Example: ControlPlane manages compute instances

#### 3. NetworkConnection Creation
Network connectivity is established through NetworkConnection entries:
- Automatic discovery of network references in resource specs
- ComputeInstance NICs connect to networks/subnetworks
- Routes connect to destination subnets
- 5G services connect based on configuration references

#### 4. Knowledge Graph Integration
Each resource also creates KgResourceDescriptionNode entries:
- `content`: Full JSON representation of the resource
- `embedding`: Vector embeddings generated using Vertex AI text-embedding-005 model
- Enables semantic search and AI-powered analysis

### Specific CRD Mappings

**Connectivity Services:**
- MeshService → NetworkNode + ResourceConnections to WireguardAppliances + NetworkConnections between all mesh participants
- PointToPointService → NetworkNode + ResourceConnections to two WireguardAppliances + NetworkConnection representing tunnel
- WireguardAppliance → NetworkNode + NetworkConnections to peer appliances and attached networks

**5G Services:**
- ControlPlane → NetworkNode + ResourceConnections to referenced UserPlaneFunction and DataNetwork
- UserPlaneFunction → NetworkNode + NetworkConnections to ingress/egress networks
- UERanSIM → NetworkNode + ResourceConnections to ControlPlane + NetworkConnections to attached networks
- UETest → NetworkNode + ResourceConnections to UERanSIM and DataNetwork for traffic simulation

**GCP Resources:**
All GCP Compute resources (ComputeInstance, ComputeNetwork, ComputeSubnetwork, ComputeFirewall, etc.) are also mapped to NetworkNodes with automatic network connectivity discovery.

This automated mapping ensures that the complete network topology, from high-level services down to individual compute resources, is accurately represented in the Spanner graph database for analysis and management.
