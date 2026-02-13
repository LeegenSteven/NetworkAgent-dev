# CICD

The network agent demo uses a gitops approach to making changes to the network. A git repository masters the intended state of the network. [Config Sync](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/overview) reconciles the network agent git repository with our GKE orchestration cluster, adding/deleting/updating our network services and functions based on what is already deployed.

## GitOps Pipeline

The network agent gitops pipeline is shown in the figure below. 

![cicd pipeline](/docs/drawings/lifecycle/CICD.drawio.svg)

There are two k8s operators that execute changes on the network, as follows: 

* [Config connector](https://cloud.google.com/config-connector/docs/overview): making changes to GCP infrastructure
* [Network Service Operator](/operator/Readme.md): Making network function changes, often requesting intents from config connector as part of network function lifecycle. 

These operators publish a set of CRDs for the resource lifecycles they manage. External systems or users can add Config Connector or Network Service Custom Resources to gitea, and config sync will trigger the orchestration process within both operators. 
