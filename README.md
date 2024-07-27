# NetworkAgent

Demonstrate the orchestration of a simple Multi Cloud VPN service using Cloud Native orchestration techniques in GCP. 

The initial service is a simple VPN connecting VPCs over an IPSEC tunnel. A pair of VyOS router VMs are connected to a set of VPCs and to the Internet. An IPSec tunnel is configured between the VyOS routers and appropriate routing tables updated in the VPCs to connect to route traffic. 

![Simple IP Sec Tunnel](drawings/vpnflow..drawio.svg)

A single GCP environment is used to demonstrate the orchestration of all the Cloud moving parts from scratch needed to bring the connectivity service into an operational state. 

![Final environment](drawings/simple-service.drawio.svg)

To run the demo, do the following:

* [Setup GCP environment](environment/Readme.md)
* [Deploy the VyOS Edge Appliance Operator](edge-operator-kopf/Readme.md)
* [Deploy a simple connectivity service](sample-service/Readme.md)