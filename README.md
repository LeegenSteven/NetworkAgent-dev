# Network Agent Demonstration

## Overview

**Network Agent** is an autonomous network management and operations platform that leverages Graph Neural Networks (GNNs) and AI agents to intelligently monitor, analyze, troubleshoot, and manage complex telecommunications networks. The system provides real-time network topology understanding, automated fault detection, intelligent incident correlation, and autonomous network operations capabilities.

### Key Features

- **AI-Powered Network Intelligence**: Multiple specialized AI agents work collaboratively to manage different aspects of network operations including supervision, engineering analysis, operations management, testing, and incident resolution
- **Graph Neural Network Analytics**: Utilizes advanced GNN models (DGAT, HetGNN, ST-GNN) to learn network topology representations and detect anomalies in network behavior
- **L3VPN Management**: Full lifecycle management of Layer 3 VPN services including hub-and-spoke and full-mesh topologies on VyOS routers
- **Automated Fault Detection & Correlation**: Real-time log and metrics collection with intelligent fault correlation and incident management
- **Network Topology Service**: Property graph-based network model stored in Google Cloud Spanner with support for physical and logical network layers
- **Interactive Dashboard**: Flutter-based web dashboard for network visualization and management
- **Traffic Testing Framework**: Automated network connectivity and performance testing capabilities

### Architecture

The system is built on a cloud-native architecture running on Google Kubernetes Engine (GKE) and includes:

- **Network Agents**: Specialized agents for supervisor, operations, engineering, testing, logs analysis, and incident management
- **Operator**: Kubernetes operator managing custom resources for routers, VPNs, traffic tests, and network infrastructure
- **GNN Models**: Training and serving infrastructure for graph neural network models
- **Log & Metrics Services**: Real-time collection and processing of network telemetry data
- **Tools Service**: Network analysis and diagnostic tools
- **Database**: Google Cloud Spanner for storing network topology graphs with native property graph support

### Technology Stack

- **Infrastructure**: Google Cloud Platform (GKE, Cloud Run, Spanner, BigQuery, Cloud Logging)
- **Machine Learning**: PyTorch-based GNN models with custom architectures for network anomaly detection
- **Network Virtualization**: VyOS routers, Free5GC core network
- **Orchestration**: Kubernetes with custom operators and Config Connector
- **Languages**: Python (agents, operators, ML), Dart/Flutter (UI), Ruby (operator logic)
- **Storage**: Cloud Spanner property graphs, BigQuery analytics

## Run the demo

* [Installation Instructions](/INSTALL.md)

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).