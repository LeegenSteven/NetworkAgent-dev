# Telco Lab Network - Operator Decomposition Architecture

This lab demonstrates realistic telco network topologies using VyOS routers with simplified VRF routing (no MPLS), implemented using a **single-source-of-truth** approach where the VyOSNetwork operator decomposes the network definition into individual VyOSRouter and DockerNetwork resources.

**Available Network Topologies:**
- **l3vpn-hub-spoke.yaml**: Hub-and-spoke L3VPN topology for centralized services (main architecture)
- **vyos-network-mpls.yaml.backup**: Original MPLS L3VPN configuration (reference)

## Architecture Overview

### Operator Decomposition Flow

```
┌─────────────────┐    Decomposes    ┌─────────────────┐
│   VyOSNetwork   │ ───────────────► │ VyOSNetwork     │
│   (Single CR)   │                  │ Operator        │
└─────────────────┘                  └─────────────────┘
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │   Generates     │
                                     └─────────────────┘
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                     ▼                     ▼
                ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                │  VyOSRouter     │   │  DockerNetwork  │   │  VyOSRouter     │
                │  Operator       │   │  Operator       │   │  Operator       │
                └─────────────────┘   └─────────────────┘   └─────────────────┘
                        │                     │                     │
                        ▼                     ▼                     ▼
                ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                │ Router Pods     │   │ Docker Networks │   │ Router Pods     │
                │ & Configs       │   │ & Connectivity  │   │ & Configs       │
                └─────────────────┘   └─────────────────┘   └─────────────────┘
```

### Key Benefits

1. **Single Source of Truth**: All network topology, router configurations, and connectivity defined in one VyOSNetwork CR
2. **Operator Decomposition**: VyOSNetwork operator automatically generates individual VyOSRouter and DockerNetwork CRs
3. **Separation of Concerns**: Each operator handles its specific lifecycle (network topology vs router instances vs network connectivity)
4. **Simplified Management**: Users only need to manage one comprehensive resource
5. **Automatic Consistency**: Network topology and router configurations are always in sync

## Network Architecture

```
    ┌─────────────────────────────────────────────────────────┐
    │                  CORE NETWORK (AS 65001)               │
    │                                                         │
    │              [Core-1]────────[Core-2]                  │
    │                 │              │                       │
    │                 │              │                       │
    │              [PE-HUB]       [PE-SPOKE]                 │
    │                 │              │                       │
    └─────────────────┼──────────────┼───────────────────────┘
                      │              │
    ┌─────────────────┼──────────────┼───────────────────────┐
    │                 │              │     ACCESS LAYER     │
    │              [HUB-AGG]      [SPOKE-AGG]               │
    │                 │              │                       │
    │           [HUB-SERVICES]   [CPE-SPOKE-1] [CPE-SPOKE-2] │
    │                 │              │             │         │
    │           Central Data      Customer      Customer     │
    │           Center/Hub        Site-1        Site-2       │
    └─────────────────────────────────────────────────────────┘
```

## Custom Resource Definitions

### VyOSNetwork (Master Resource)
**Purpose**: Single source of truth containing complete network topology and all router definitions

**Key Features**:
- **Networks**: All network segments with router connections and IP assignments
- **Routers**: Complete router definitions with interfaces, protocols, services
- **QoS Policies**: Traffic management policies referenced by routers
- **MPLS L3VPN**: Customer separation and VRF configurations
- **Security**: Firewall policies for network protection
- **Routing**: OSPF areas and BGP configuration

**Operator Behavior**: The VyOSNetwork operator watches for VyOSNetwork resources and automatically:
1. Validates the complete network topology
2. Generates individual VyOSRouter CRs for each router
3. Generates DockerNetwork CRs for each network segment
4. Manages the lifecycle and dependencies between resources

### VyOSRouter (Generated Resource)
**Purpose**: Individual router instance generated from VyOSNetwork

**Key Features**:
- **Source Tracking**: References the source VyOSNetwork that generated it
- **Simplified Schema**: Contains only the configuration needed for this specific router
- **Interface Mapping**: Interfaces mapped to specific DockerNetwork resources
- **Protocol Configuration**: Router-specific OSPF, BGP, MPLS settings
- **Status Tracking**: Individual router deployment and operational status

**Operator Behavior**: The VyOSRouter operator:
1. Creates and manages VyOS container instances
2. Applies router-specific configurations via Ansible
3. Connects interfaces to appropriate Docker networks
4. Monitors router health and protocol status

### DockerNetwork (Generated Resource)
**Purpose**: Network connectivity between routers generated from VyOSNetwork

**Key Features**:
- **Source Tracking**: References the source VyOSNetwork that generated it
- **Simplified Schema**: Contains only network-specific configuration
- **Router Connections**: Lists all routers connected to this network
- **IPAM Configuration**: IP address management for the network
- **Network Properties**: MTU, VLAN, bandwidth, and topology type

**Operator Behavior**: The DockerNetwork operator:
1. Creates Docker networks with proper IPAM configuration
2. Manages network lifecycle and connectivity
3. Handles VLAN tagging and network isolation
4. Monitors network status and connectivity

## Deployment Workflow

### 1. Deploy the Hub-and-Spoke Network Using Operator
```bash
# Deploy the hub-and-spoke network definition using the operator
python operator/src/main.py --config telco-lab/l3vpn-hub-spoke.yaml
```

### 2. Run Automated Tests
```bash
# Execute the comprehensive test script to verify the network is working
./telco-lab/test-hub-spoke.sh
```

The test script automatically validates:
- **Infrastructure**: All router containers and Docker networks are running
- **Basic Connectivity**: P2P links, access networks, and CPE connectivity
- **OSPF Protocol**: Neighbor relationships and convergence
- **BGP Protocol**: Sessions and VRF configuration
- **Hub-and-Spoke Connectivity**: Spoke-to-hub and hub-to-spoke communication
- **Centralized Services**: Access to hub services from spoke sites
- **Security Isolation**: Traffic routing through hub
- **Management Network**: Out-of-band management access
- **Interface Status**: All router interfaces operational

The script provides colored output with pass/fail indicators and a summary report.

### 3. Monitor Deployment Progress
```bash
# Check running router containers
docker ps --filter "label=vyos.router.name"

# Check Docker networks created
docker network ls | grep l3vpn-hub-spoke

# Check router container logs
docker logs core-1
docker logs pe-hub
docker logs pe-spoke

# Check container resource usage
docker stats --no-stream
```

### Alternative: Deploy MPLS Reference Network
```bash
# Deploy the original MPLS L3VPN configuration (reference implementation)
python operator/src/main.py --config telco-lab/vyos-network-mpls.yaml.backup

# Note: This configuration requires MPLS-capable VyOS images
```

## Network Components

### Hub-and-Spoke Topology Components

#### Router Inventory (7 Total)
- **Core-1** (10.0.0.11): Core router with OSPF
- **Core-2** (10.0.0.12): Core router with OSPF
- **PE-HUB** (10.0.0.1): Hub PE router with VRF routing and iBGP
- **PE-SPOKE** (10.0.0.2): Spoke PE router with VRF routing and iBGP
- **HUB-AGG** (10.0.0.21): Hub aggregation router
- **SPOKE-AGG** (10.0.0.22): Spoke aggregation router
- **HUB-SERVICES** (10.0.0.31): Hub services router (data center gateway)
- **CPE-SPOKE-1** (10.0.0.41): Customer spoke site 1, AS 65100
- **CPE-SPOKE-2** (10.0.0.42): Customer spoke site 2, AS 65100

#### Hub Services
- **Data Center Gateway**: Centralized application and database access
- **Internet Gateway**: Shared internet access for all spoke sites
- **Security Services**: Centralized firewall and intrusion detection
- **DNS/DHCP Services**: Centralized network services
- **Backup Services**: Centralized backup and disaster recovery

#### End Customer Devices
**Hub Site Devices:**
- **hub-server-1** (172.16.100.10): Central application server
- **hub-server-2** (172.16.100.20): Central database server

**Spoke Site Devices:**
- **spoke-1-device** (172.16.1.100): Spoke site 1 workstation
- **spoke-2-device** (172.16.2.100): Spoke site 2 workstation

**Device Specifications:**
- **Base Image**: Alpine Linux with networking tools
- **Tools Installed**: ping, traceroute, curl, netcat, iperf3, tcpdump, mtr
- **Resources**: 100m CPU, 128Mi memory per device
- **Purpose**: End-to-end network testing and validation

### Network Segments
- **Core P2P Links**: 10.1.1.0/30, 10.1.2.0/30, 10.1.3.0/30
- **Hub Access**: 10.1.10.0/30
- **Spoke Access**: 10.1.20.0/30
- **Hub Services**: 172.16.100.0/24
- **Spoke Sites**: 172.16.1.0/24, 172.16.2.0/24
- **Management**: 192.168.122.0/24 (`network_type: "management"`, always on `eth0`)

## Key Features

### Hub-and-Spoke Architecture Benefits
- **Centralized Services**: Shared data center, internet gateway, security services
- **Cost Efficiency**: Reduced bandwidth requirements between spoke sites
- **Security Control**: Centralized firewall and security policies
- **Service Concentration**: Shared servers, databases, and applications at hub
- **Simplified Management**: Single point for service deployment and monitoring

### VRF-Based Customer Separation (Simplified - No MPLS)
- **Customer A**: Sites connected via VRF routing tables (VRF table 100)
- **Customer B**: Sites connected via VRF routing tables (VRF table 200)
- Full customer traffic separation using VRFs
- Simple BGP for VRF route exchange between PE routers (no VPNv4)

### Routing Protocols
- **OSPF**: Multi-area IGP design
  - Area 0.0.0.0: Backbone (Core and PE routers)
  - Area 0.0.0.1: Access area 1 (PE-1 and AGG-1)
  - Area 0.0.0.2: Access area 2 (PE-2 and AGG-2)
- **BGP**: Simple iBGP between PE routers for VRF route exchange
  - PE-1 peers with PE-2 via iBGP (AS 65001)
  - CPE routers peer with PEs via eBGP (AS 65100, AS 65200)
  - No route reflectors, no VPNv4 address-family

### Quality of Service (QoS)
- **ACCESS-QOS**: 1Gbps policies for access links
  - Voice (15%), Video (25%), Data (60%)
- DSCP marking and traffic prioritization

### Security Features
- **Customer Separation**: VRF-based isolation with firewall policies
- **CPE Isolation**: Prevents inter-CPE communication
- **Management Network**: Separate OOB management for all devices

### Interface Assignment Convention
- **Management Interface**: All routers use `eth0` for management connectivity
  - Networks with `network_type: "management"` are always assigned to `eth0`
  - Provides consistent out-of-band management access across all devices
- **Service Interfaces**: Data plane traffic uses `eth1`, `eth2`, `eth3`, etc.
  - Interface assignments are automatically shifted to accommodate management on `eth0`
  - Ensures predictable interface mapping for network operations

### Hub-and-Spoke Specific Features

#### Centralized Service Access
- **Hub VRF**: All spoke sites can access hub services (VRF table 100)
- **Internet Access**: Spoke sites route internet traffic through hub
- **Shared Resources**: Central servers, databases, and applications
- **Service Chaining**: Traffic flows through hub for security inspection

#### Routing Policy
- **Spoke-to-Hub**: Full connectivity to hub services and internet
- **Spoke-to-Spoke**: Traffic routes through hub (no direct spoke-to-spoke connectivity)
- **Hub-to-Spoke**: Full management and service access to all spokes
- **Route Leaking**: Selective route advertisement between VRFs

#### Traffic Flow Patterns
- **Spoke → Hub**: Direct connectivity for centralized services
- **Spoke → Internet**: Routed through hub gateway
- **Spoke ↔ Spoke**: Indirect connectivity via hub routing
- **Hub → Anywhere**: Full connectivity to all network segments

## Operator Implementation Details

### VyOSNetwork Operator Logic
1. **Validation Phase**:
   - Validate network topology consistency
   - Check IP address assignments and overlaps
   - Verify router interface mappings
   - Validate protocol configurations
   - Ensure exactly one network has `network_type: "management"`
   - Validate management network interface assignments (eth0)

2. **Decomposition Phase**:
   - Generate VyOSRouter CRs from router definitions
   - Generate DockerNetwork CRs from network definitions
   - Set proper ownership references for garbage collection
   - Apply consistent labeling and annotations

3. **Orchestration Phase**:
   - Create DockerNetwork resources first (dependencies)
   - Create VyOSRouter resources with proper sequencing
   - Monitor child resource status and update parent status
   - Handle updates and deletions with proper cleanup

### Resource Relationships
```yaml
VyOSNetwork "l3vpn-hub-spoke-network"
├── owns: DockerNetwork "core-pe-hub-core1"
├── owns: DockerNetwork "core1-core2"
├── owns: DockerNetwork "core-core2-pe-spoke"
├── owns: DockerNetwork "pe-hub-hub-agg"
├── owns: DockerNetwork "pe-spoke-spoke-agg"
├── owns: DockerNetwork "hub-agg-hub-services"
├── owns: DockerNetwork "..."
├── owns: VyOSRouter "core-1"
├── owns: VyOSRouter "core-2"
├── owns: VyOSRouter "pe-hub"
├── owns: VyOSRouter "pe-spoke"
├── owns: VyOSRouter "hub-agg"
├── owns: VyOSRouter "spoke-agg"
├── owns: VyOSRouter "hub-services"
├── owns: VyOSRouter "cpe-spoke-1"
└── owns: VyOSRouter "cpe-spoke-2"
```

## Verification and Testing

### Hub-and-Spoke Specific Testing

#### Hub Services Connectivity
```bash
# Test spoke site access to hub services
docker exec -it cpe-spoke-1 ping 172.16.100.10 -c 3  # Hub server 1
docker exec -it cpe-spoke-2 ping 172.16.100.20 -c 3  # Hub server 2

# Test hub access to spoke sites
docker exec -it hub-services ping 172.16.1.100 -c 3  # Spoke 1 device
docker exec -it hub-services ping 172.16.2.100 -c 3  # Spoke 2 device

# Verify spoke-to-spoke connectivity (should route through hub)
docker exec -it cpe-spoke-1 traceroute 172.16.2.100
docker exec -it cpe-spoke-2 traceroute 172.16.1.100

# Test centralized internet access
docker exec -it spoke-1-device ping 8.8.8.8 -c 3
docker exec -it spoke-2-device ping 8.8.8.8 -c 3
```

#### Service Validation
```bash
# Test DNS resolution through hub
docker exec -it spoke-1-device nslookup google.com 172.16.100.10

# Test centralized web services
docker exec -it spoke-1-device curl -I http://172.16.100.10
docker exec -it spoke-2-device curl -I http://172.16.100.20

# Verify bandwidth aggregation at hub
docker exec -it hub-server-1 iperf3 -s &
docker exec -it spoke-1-device iperf3 -c 172.16.100.10 -t 10 &
docker exec -it spoke-2-device iperf3 -c 172.16.100.10 -t 10
```

#### Hub-and-Spoke Routing Verification
```bash
# Verify routing tables show hub as next hop for spoke-to-spoke traffic
docker exec -it cpe-spoke-1 vtysh -c 'show ip route'
docker exec -it cpe-spoke-2 vtysh -c 'show ip route'

# Check BGP sessions between hub and spokes
docker exec -it pe-hub vtysh -c 'show bgp summary'
docker exec -it pe-spoke vtysh -c 'show bgp summary'

# Verify VRF route exchange
docker exec -it pe-hub vtysh -c 'show ip route vrf CUSTOMER-HUB'
docker exec -it pe-spoke vtysh -c 'show ip route vrf CUSTOMER-SPOKE'
```

#### Customer Connectivity Testing
```bash
# Test Customer A site-to-site connectivity
docker exec -it cpe-1 ping 172.16.2.10 -c 3
docker exec -it cpe-2 ping 172.16.1.10 -c 3

# Test Customer B site-to-site connectivity
docker exec -it cpe-3 ping 172.17.2.10 -c 3
docker exec -it cpe-4 ping 172.17.1.10 -c 3

# Verify customer separation (should fail)
docker exec -it cpe-1 ping 172.17.1.10 -c 3
docker exec -it cpe-3 ping 172.16.1.10 -c 3

# Test access network connectivity
docker exec -it cpe-1 ping 10.10.1.1 -c 3  # AGG-1 gateway
docker exec -it cpe-2 ping 10.10.2.1 -c 3  # AGG-2 gateway
```

#### End-to-End Customer Device Testing
```bash
# List customer device containers
docker ps --filter "label=customer"

# Test Customer A site-to-site connectivity from end devices
docker exec -it cust-a-device-1 ping 172.16.2.100 -c 3
docker exec -it cust-a-device-3 ping 172.16.1.100 -c 3

# Test Customer B site-to-site connectivity from end devices
docker exec -it cust-b-device-1 ping 172.17.2.100 -c 3
docker exec -it cust-b-device-3 ping 172.17.1.100 -c 3

# Verify customer isolation (should fail)
docker exec -it cust-a-device-1 ping 172.17.1.100 -c 3
docker exec -it cust-b-device-1 ping 172.16.1.100 -c 3

# Test bandwidth between Customer A sites using iperf3
# Start iperf3 server on Site 2 device
docker exec -d cust-a-device-3 iperf3 -s

# Run iperf3 client from Site 1 device
docker exec -it cust-a-device-1 iperf3 -c 172.16.2.100 -t 10

# Trace network path through MPLS core
docker exec -it cust-a-device-1 traceroute 172.16.2.100
docker exec -it cust-b-device-1 traceroute 172.17.2.100

# Test network tools availability
docker exec -it cust-a-device-1 mtr --report --report-cycles 5 172.16.2.100

# Test port connectivity with netcat
docker exec -d cust-a-device-3 nc -l -p 8080
docker exec -it cust-a-device-1 nc -zv 172.16.2.100 8080

# Packet capture for troubleshooting
docker exec -it cust-a-device-1 tcpdump -i any -c 10 icmp

# Test connectivity to CPE gateways
docker exec -it cust-a-device-1 ping 172.16.1.10 -c 3  # CPE-1 gateway
docker exec -it cust-a-device-3 ping 172.16.2.10 -c 3  # CPE-2 gateway
```

#### Advanced Testing Scenarios
```bash
# Test QoS and traffic shaping
# Generate different traffic types and measure performance

# Voice traffic simulation (high priority)
docker exec -it cust-a-device-1 iperf3 -c 172.16.2.100 -u -b 64k --tos 0xb8

# Video traffic simulation (medium priority)
docker exec -it cust-a-device-1 iperf3 -c 172.16.2.100 -u -b 2M --tos 0x88

# Best effort traffic (low priority)
docker exec -it cust-a-device-1 iperf3 -c 172.16.2.100 -u -b 10M --tos 0x00

# Test DNS resolution through the network
docker exec -it cust-a-device-1 nslookup google.com

# Test HTTP connectivity through NAT
docker exec -it cust-a-device-1 curl -I http://httpbin.org/ip

# Monitor container resource usage
docker stats --no-stream


# Test MPLS path with traceroute
docker exec -it cpe-1 traceroute 172.16.2.10
docker exec -it cpe-3 traceroute 172.17.2.10
```

#### Customer Device Management
```bash
# List all hub and spoke device containers
docker ps --filter "name=hub-server"
docker ps --filter "name=spoke-device"

# List all router containers
docker ps --filter "label=vyos.router.name"

# Check container logs
docker logs hub-server-1
docker logs cpe-spoke-1
docker logs pe-hub

# Execute interactive shell on a device
docker exec -it spoke-1-device /bin/sh
docker exec -it cpe-spoke-1 vbash

# Check container resource usage
docker stats hub-server-1 hub-server-2 spoke-1-device spoke-2-device

# Inspect container network configuration
docker exec -it cpe-spoke-1 ip addr show
docker exec -it pe-hub ip addr show

# Check Docker network connectivity
docker network ls | grep l3vpn-hub-spoke
docker network inspect l3vpn-hub-spoke-hub-services

# Additional Docker-specific verification commands
# Check all containers in the lab
docker ps -a --filter "label=vyos.source.network=l3vpn-hub-spoke-network"

# Verify container network attachments
docker inspect pe-hub | jq '.[0].NetworkSettings.Networks'
docker inspect core-1 | jq '.[0].NetworkSettings.Networks'

# Check container interface configuration
docker exec -it pe-hub ip route show
docker exec -it core-1 ip route show

# Monitor real-time container logs
docker logs -f pe-hub &
docker logs -f core-1 &

# Test management network connectivity
docker exec -it pe-hub ping 192.168.122.1 -c 3  # PE-HUB management IP
docker exec -it core-1 ping 192.168.122.11 -c 3  # Core-1 management IP

# Verify VyOS configuration persistence
docker exec -it pe-hub vbash -c 'show configuration'
docker exec -it core-1 vbash -c 'show configuration'

# Check system resources across all containers
docker system df
docker system events --filter container=pe-hub --filter container=core-1
```

## Use Cases for Hub-and-Spoke Architecture

### Primary Use Cases
- **Corporate Networks**: Branch offices connecting to headquarters
- **Retail Chains**: Store locations connecting to central data center
- **Manufacturing**: Factory sites connecting to central ERP systems
- **Healthcare**: Clinic sites connecting to central patient records
- **Government**: Field offices connecting to central services
- **Cloud Migration**: Hybrid cloud connectivity with centralized internet egress

### Technical Benefits
- **Simplified Routing**: Fewer BGP peers and simpler routing configuration
- **Cost-Effective**: Reduces the number of required inter-site links
- **Centralized Security**: Single point for security policy enforcement
- **Bandwidth Optimization**: Efficient use of expensive WAN links
- **Service Aggregation**: Centralized hosting of shared services

### Considerations
- **Single Point of Failure**: Hub failure affects all spoke connectivity
- **Potential Bottleneck**: All inter-spoke traffic routes through hub
- **Latency Impact**: Additional hop for spoke-to-spoke communication
- **Bandwidth Planning**: Hub links must accommodate aggregated traffic

This architecture provides a robust foundation for telco network orchestration using cloud-native principles while maintaining the complexity and feature richness required for production telco environments. The hub-and-spoke topology offers a cost-effective and manageable solution for organizations requiring centralized services and simplified network management.
