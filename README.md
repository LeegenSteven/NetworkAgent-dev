# Network Agent Demonstration

The Network Agent is an interactive AI service that helps Cloud Architects easily create and manage Enterprise connectivity services. The network agent demo shows the following:

* Cloud native orchestration of an Enterprise connectivity service, deploying a set of virtual routers on GCP to connect IT applications across locations.
* Natural language chat interface to discover, design and update Enterprise connectivity services.

![demo system](drawings/system.drawio.svg)

The main components of the demo can be seen in the figure above. 

* __Chat Interface__: Multi modal chat interface to allow Cloud architects to use natural language and images to manage their network services
* __Network Agent__: GenAI agent that can run a set of tools to report on and make changes to a customers network services
* __Cloud Native Service Orchestration__: Kubernetes based orchestration of cloud, connectivity service and virtual network function resources
* __Service Monitoring__: Collection of virtual network function metrics for performance and fault monitoring. 

## Network Agent Use Cases

The Network Agent provides a natural language interface to allow an Enterprise customer to create, update and view their multi cloud connectivity services. Simplifying the experience of designing and maintaining complex connectivity services. 

The network agent support the following use cases:

* Ask for a description of available connectivity services that can be deployed
* Request a new instance of a connectivity service. Interacting with the Agent to provide the required information to instantiate the chosen service. Confirm all connectivity design decisions and confirm the 
* Update an existing instance of a connectivity service. Interacting with the Agent to ensure all required information is collected and confirming the exection of agreed changes
* View existing services and their configuration
* View monitoring statistics for one or more connectivity services

## Demo Architecture

The following links detail how the demo is architected.

* [Supported Network Services](docs/networkservices.md)
* [Virtual Network Functions](docs/wireguard-vnf.md)
* [GCP Environment](docs/gcp.md)
* [Lifecycle Management](docs/lifecycle.md)
* [CICD of Network Services](docs/cicd.md)
* [Network Agent](docs/agent.md)

## Create the demo environment

The following links describe how to get the demo environment up and running.

* [Setup GCP environment](environment/Readme.md)
* [Build and deploy the network operator](/operator/Readme.md)
* [Build and deploy the network agent REST tools](/tools/Readme.md)
* [Build and deploy the network agent](/networkagent/Readme.md)

## Running the demo

* Find services available to deploy
* Deploy a service
* Query services that are deployed
* Query Performance of deployed services
* Run a Test across the VPN
* Query Performance of deployed services