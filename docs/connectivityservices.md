# Connectivity Network Services

The demo provides a set of connectivity network services that can connect traffic from one or more VPCs, i.e. 

* __Site to site__: connecting 2 sites together with a VPN tunnel
* __Mesh__: connecting 3 or more sites in a full mesh
* __Muti hop__: connecting 2 or more sites across a series of locations

The sections below describe the network services and virtual network function in more detail.

## Site to Site

This service connects two private VPCs over a wireguard tunnel. A pair of virtual network functions (VNFs) are connected to the two provided private VPCs. A wireguard tunnel is configured between the VNFs and static routes added to the private VPCs to route traffic over the VPN tunnel.

![Simple VPN Tunnel](/drawings/connectivity/ptpflow..drawio.svg)

## Mesh

This service connects three or more VPCs over a set of wireguard tunnels. All VPCs can route traffic to/from the other VPCs in the Mesh.

![Mesh VPN Tunnel](/drawings/connectivity/meshflow.drawio.svg)

## Multi Hop (To be done)

https://www.procustodibus.com/blog/2022/06/multi-hop-wireguard/

## Wireguard VNF

The virtual network function used in this demo is based on opensource linux software, i.e. ubuntu, wireguard, iptables etc. In later iterations of the demo, 3rd party virtual firewalls/VPN software will be added.

![VNF](/drawings/connectivity/vnf.drawio.svg)

As seen in the diagram above, the VNF is configured with 3 network interfaces

* __Mgmt__: All communication with the VNF for configuration and monitoring is carried over this interface. 
* __Customer VPC__: The pre-existing customer VPC to connect the VNF to and route traffic to/from
* __Dataplane__: The VPC dedicated to carrying the VPC traffic, connecting a collection of VNFs

A wireguard virtual network interface is created, connecting pairs of VNFs over the dataplane network. All allowed traffic from each VNF is routed between the customer network interface and the wireguard virtual interface. 
