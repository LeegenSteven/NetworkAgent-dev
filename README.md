# Network Agent Demonstration

**Network Agent** is an autonomous network management and operations platform that leverages Graph Neural Networks (GNNs) and AI agents to intelligently monitor, analyze, troubleshoot, and manage complex telecommunications networks. The system provides real-time network topology understanding, automated fault detection, intelligent incident correlation, and autonomous network operations capabilities.

### Architecture

The network agent system is built on a cloud-native architecture and includes:

- **Network Agents**: Specialized agents for network testing, log analysis, and incident management
- **Operator**: Kubernetes operator managing custom resources for virtual routers, network services, traffic tests, and network infrastructure
- **GNN Models**: Training and serving infrastructure for graph neural network models that can pinpoint failures and predict the impact of network changes. 
- **Log & Metrics Services**: Real-time collection and processing of network telemetry data
- **Digital Twin**: Google Cloud Spanner for storing network topology graphs and temporal state 


![gcp architecture](/docs/drawings/gcp.drawio.svg)

The diagram above shows the end-to-end GCP architecture. Network users interact with a Dashboard which connects to a set of AI Agents that communicate with each other over the a2a protocol. The agents query Google Cloud Spanner for network topology and metrics, and coordinate with a Kubernetes Operator on GKE that manages custom resources (CRDs) for routers, VPNs, and network infrastructure. 

GKE Config Connector and an Orchestration Operator provision and configure virtual network resources on a GCE VM running VyOS router and CPE containers. That VM pushes logs and metrics to Cloud Monitoring, which feeds into Pub/Sub → Eventarc → Cloud Functions to propagate topology changes back into Spanner. Spanner also provides periodic training snapshots to Vertex AI, where GNN models are trained and served for network analytics.

## Virtual Network 

The virtual network runs [VyOS routers](https://vyos.io/) as containers inside a GCE VM, interconnected via VLAN subinterfaces and veth pairs. 

Telemetry (syslog and Prometheus metrics) is collected by an Ops Agent and routed through Cloud Monitoring and Eventarc into the Spanner network graph. 

A management VPC provides out-of-band access to the VM. The diagram below shows an example deployment with two VyOS routers (vyos1, vyos2), two CPEs, and the associated bridge networks and virtual interfaces.

![virtual network](/docs/drawings/networking.drawio.svg)

The diagram below illustrates a hub-and-spoke L3VPN topology provided as an example by the system. Two spoke sites connect via CE routers to Provider Edge (PE) routers at 100 Mbps, which feed into an MPLS core running OSPF, LDP, and iBGP (AS 65001) across four P-routers at 1 Gbps. Route Reflectors (RR 1 and RR 2) distribute iBGP routes across the core. VRF routing policy is defined by `BLUE_HUB` (imports routes from both hub and spoke targets) and `BLUE_SPOKE` (imports only hub routes), enabling hub-controlled traffic flow between spokes.

![l3vpn](/docs/drawings/transport/l3vpn-example.drawio.svg)

## Run the demo

* [Installation Instructions](/INSTALL.md)

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).