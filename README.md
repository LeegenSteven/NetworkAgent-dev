# Network Agent Demonstration

The Network Agent is an interactive AI service that helps Cloud Architects easily create and manage Enterprise connectivity services. The network agent demo shows the following:

* Cloud native orchestration of an Enterprise connectivity service, deploying a set of virtual routers on GCP to connect IT applications across locations.
* Natural language chat interface to auto-design and update Enterprise connectivity services.

The main components of the demo can be seen in the figure below. 

![demo system](drawings/system.drawio.svg)

The main functional components of the demo are as follows:

* Chat Interface
* Network Agent
* Cloud Native Service Orchestration
* Service Monitoring


## Demo Network Service

The initial service is a simple VPN connecting VPCs over an IPSEC tunnel. A pair of VyOS router VMs are connected to a set of VPCs and to the Internet. An IPSec tunnel is configured between the VyOS routers and appropriate routing tables updated in the VPCs to connect to route traffic. 

![Simple IP Sec Tunnel](drawings/vpnflow..drawio.svg)

A single GCP environment is used to demonstrate the orchestration of all the Cloud moving parts from scratch needed to bring the connectivity service into an operational state. 

![Final environment](drawings/simple-service.drawio.svg)

## Running the demo

To run the demo, do the following:

* [Setup GCP environment](environment/Readme.md)
* [Deploy the VyOS Edge Appliance Operator](operator/Readme.md)
* [Test a simple connectivity service](sample-service/Readme.md)
* [Run the Network Agent](networkagent/Readme.md)