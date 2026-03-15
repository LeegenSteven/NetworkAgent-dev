# Network Prediction Environment

**Network Prediction** is an autonomous network lifecycle management platform that leverages Graph Neural Networks (GNNs) and AI agents to intelligently monitor, analyze, troubleshoot, and manage complex telecommunications networks. The system provides a virtual network simulator with real-time network topology understanding, automated fault detection, intelligent incident correlation and resolution capabilities.

### Architecture

The main components of the network prediction architecture are shown below:

![gcp architecture](/docs/drawings/architecture.drawio.svg)

- [**Network Simulator**](/docs/network/Readme.md): [VyOS](https://vyos.io/) & [free5gc](https://free5gc.org/) based virtual network simulator can deploy complex transport and mobile network topologies, run traffic patterns and generate network state and performance metrics. 
- [**GKE Network Automation**](/docs/automation/Readme.md): GKE operator deploys network topologies and traffic tests to the network simulator and updates the Spanner digital shadow with topology and state updates.
- [**Digital Shadow**](/docs/spanner/Readme.md): Google Cloud Spanner stores network topology graphs, temporal state and historical performance. 
- [**Network GNNs**](/docs/gnn-research/Readme.md): Training and serving infrastructure for graph neural network models that can pinpoint failures and predict the impact of network changes. 
- [**Network Agents**](/docs/agents/Readme.md): Specialized agents for network testing, log analysis, and incident management

## Running the demo

* [Installation Instructions](/INSTALL.md)

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).