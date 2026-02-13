# GCP Environment

The demo is deployed to a single GCP project, as shown in the figure below. 

![Environment](/docs/drawings/gcp.drawio.svg)

The GCP components are as follows: 

* __Virtual Network Functions:__  Free5gc and connectivity network functions are deployed in Google Compute Engine virtual machines. All network functions are deployed to Ubuntu vanilla images, installing all required software when the network function is created. 
* __GKE Orchestration:__ Network functions and GCP infrastructure are lifecycle managed through GKE, using __config connector__ to deploy GCP components and a custom __network operator__ to deploy the network functions. The __network operator__ also triggers a topology update to spanner when something is added or deleted to the network. 
* __Monitoring:__ Logs from the orchestration tools and the network functions themselves are captured from __cloud monitoring__ and added to spanner database, available for agents to analyse later. 
* __Network Topology:__ Spanner holds the current topology of the network, all logs and performance metrics. 
* __Network Agents:__ A group of specialist network agents are orchestrated by a supervisor agent. The specialist agents all have access to a set of tools to request information from Spanner or trigger automation on GKE. The supervisor agent  communicates with a Dashboard UI through socketio. All running on Cloud Run.
