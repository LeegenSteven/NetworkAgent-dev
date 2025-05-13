# Network Services

The demo manages an end to end 5G virtual mobile network, The 5G network service topology shown below can be automated into an operational state within a single GCP project.

![virtual mobile network](/drawings/free5gc/mobile.drawio.svg)

The following virtual infrastructure can be deployed to instantiate the 5G network service. 

* __Core Network Site:__ Based on [Free5gc 5G Core network functions](/docs/free5gcservices.md)
    * GCP networks are deployed to attach DNN mimicking the Internet and a Core network attaches control plane and upf network functions
    * Free5gc Control Plane CNFs are deployed in a virtual machine attached to the core network. 
    * Free5gc UPF VNF is deployed attached to core and internet networks, routing between the two. 
* __Radio Sites:__ [Running Radio simulators](https://github.com/aligungr/UERANSIM)
    * GCP network per radio "site"
    * UERANSIM gNB Radio Network Simulator VNF is attached to the cellsite network. UE simulators can establish sessions and test traffic routed through the 5G network to the internet services above. 
* __VPN Services:__ [Connecting all sites](/docs/connectivityservices.md)
    * Wireguard VNFs creat a set of tunnels in a mesh or point to point configuration between GCP networks.

Once deployed test traffic can be run from the simulated UEs across the network to the Internet. 

## Network Service Model

All the components of network services are modelled in the Spanner topology database. The diagram below shows the main components of the network service model.

![network service model](/drawings/graph/model.drawio.svg)

Each component is described in the table below. 

| Component | Description | 
|-----------|-------------|
| Network Service | Top level customer facing service  |
| Resource Facing Service | Specific instantiation for a particular customer, capturing the logical design of how the service is delivered. Resource facing service      | 
| Logical Resource | Logical network elements (software constructs) and their configurations, can be specialised to Virtual or Phyiscal Resources |  
| Virtual Resources | Virtual devices and their components, e.g. compute instance, compute route, compute subnetwork, compute firewall and compute address | 
| Physical Resources | Physical devices with ports and links | 


### Simple Example

The figure below shows a simple network service topology example. 

![simple example](/drawings/graph/simple_example.drawio.svg)

## Spanner Schema

This section describes the schema in spanner to capture the network service topology. 

![schema](/drawings/graph/spanner.drawio.svg)
