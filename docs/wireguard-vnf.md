# Wireguard Virtual Network Function

The virtual network function used in this demo is based on opensource linux software, i.e. ubuntu, wireguard, iptables etc. In later iterations of the demo, 3rd party virtual firewalls/VPN software will be added.

![VNF](/drawings/vnf.drawio.svg)

As seen in the diagram above, the VNF is configured with 3 network interfaces

* __Mgmt__: All communication with the VNF for configuration and monitoring is carried over this interface. 
* __Customer VPC__: The pre-existing customer VPC to connect the VNF to and route traffic to/from
* __Dataplane__: The VPC dedicated to carrying the VPC traffic, connecting a collection of VNFs

A wireguard virtual network interface is created, connecting pairs of VNFs over the dataplane network. All allowed traffic from each VNF is routed between the customer network interface and the wireguard virtual interface. 
