# Network Agent Demonstration

The Network Agent is an interactive AI service that helps Network Architects easily deploy and manage mobile network infrastructure and software. The network agent demo shows the following:

* Cloud native orchestration of a complete mobile network service.
* Specialist network agents that can assist the full network lifecycle. 
* Natural language interface to build and test network servcices, run simulated tests and analyse performance data.

![demo system](drawings/system.drawio.svg)

The main components of the demo can be seen in the figure above, i.e. 

* __Chat Interface__: Multi modal chat interface to allow network engineers to use natural language and images to manage their network services.
* __Network Agents__: Collection of specialist agents that can build network services, run tests and analyse  network service performance. 
* __Cloud Native Service Orchestration__: Kubernetes based orchestration of cloud, connectivity service and virtual network function resources.
* __Active Topology__: Listen for infrastructure and network function lifecycle changes and update a topology graph with the current state of the network. 
* __Network Service Monitoring__: Monitor network function metrics and logs for performance and fault analysis.

## Network Agent Architecture

The following links detail the main aspects of the network agent demo system.

* [Network Services](docs/networkservices.md)
* [GCP Environment](docs/gcp.md)
* [CICD](docs/cicd.md)
* [Network Agents](docs/agent.md)

## Create the network agent environment

Following the steps below to create a network agent environment.

* [Setup GCP environment](environment/Readme.md)
* [Build and deploy the network operator](/operator/Readme.md)
* [Build and deploy the network agent](/networkagent/Readme.md)
* [Log into GitOps environment](/docs/git.md)

## Demo Scenarios

* [Build a 5G Network](/docs/5gbuilddemo.md)

# LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).