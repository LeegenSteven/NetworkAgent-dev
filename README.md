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

The following links detail how main components of the network agent demo.

* [Network Services](docs/networkservices.md)
* [Virtual Network Functions](docs/wireguard-vnf.md)
* [GCP Environment](docs/gcp.md)
* [Lifecycle Management](docs/lifecycle.md)
* [GitOps of Network Services](docs/cicd.md)
* [Network Agent](docs/agent.md)

## Create the demo environment

Following the steps below to create a network agent demo environment.

* [Setup GCP environment](environment/Readme.md)
* [Build and deploy the network operator](/operator/Readme.md)
* [Build and deploy the network agent REST tools](/tools/Readme.md)
* [Build and deploy the network agent](/networkagent/Readme.md)
* [Log into GitOps environment](/docs/git.md)

## Demo Scenario

Once the environment is up and running [(check gitea is __Running__ and log in)](/docs/git.md).

The following repositories are available:

* __root-repo__: Root repository 
* __london__: Namespace repo-sync managing the deployment of a set of 5G UPF and control plane network functions
* __dublin__: Namespace repo-sync managing the deployement of a simulated RAN network. 
* __newyork__: Namespace repo-sync managing the deployement of a simulated RAN network. 
* __core__: Namespace repo-sync managing the deployment of a mesh VPN network, connecting the Radio and Core sites. 

Each repository has one or more of the following branches: 

* __master__: this branch is reconciled with its associated namespace. 
* __location__: this branch has just the networking configuration for its associated namespace
* __networkfunction__: this branch includes all networking and network function configuration.

Initially the __master__ branch just contains a Readme.md file. The other branches can be pulled into master to show incremental configuration for demonstration purposes.

### Deploy source of truth Git repos 

The __install.sh__ script auto configures gitea's _root-repo_ to the _networkautomation_ cluster as its _root-sync_ reconciler across the cluster. 

The first step is to configure the remaining _repo-sync_ reconcilers for each additional repository. You can do this my logging into __gitea__ as described above, clicking on the _root-repo_ repository and creating a pull request that merges the _dev_ branch in the __root-repo__ repository with the _master_ branch. 

This will kick off a _root-sync_ reconciliation and once this is done you can see all the _repo-sync_ reconcilers being created in the GKE __networkautomation__ cluster.

Any kubernetes manifests that are now deployed to the master branch in each repo will be reconciled to their configured namespaces. 

### Deploy the RAN sites

in Git repo and show VPCs in GCP and Spanner

### Partially deploy control plane

cluster, control plane and UPF in London site / Spanner

### Deploy the Mesh VPN network 

and show in GCP / Spanner

### Run simulated UEs

 and show traffic flowing from both RAN sites

### Show metrics in Cloud Monitoring and BQ

