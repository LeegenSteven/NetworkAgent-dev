# Network Services

The demo supports the deployment of virtual network functions in a number of service topologies, e.g. 

* __Site to site__: connecting 2 sites together with a VPN tunnel
* __Mesh__: connecting 3 or more sites in a full mesh
* __Muti hop__: connecting 2 or more sites across a series of locations

The Network Agent is trained on the information needed to deploy each of these servies and also how to monitor their performance from the metrics captured. The sections below describe the network services and virtual network function in more detail.

## Site to Site

This service connects two private VPCs over a wireguard tunnel. A pair of virtual network functions (VNFs) are connected to the two provided private VPCs. A wireguard tunnel is configured between the VNFs and static routes added to the private VPCs to route traffic over the VPN tunnel.

![Simple VPN Tunnel](/drawings/services/ptpflow..drawio.svg)

## Mesh

This service connects three or more VPCs over a set of wireguard tunnels. All VPCs can route traffic to/from the other VPCs in the Mesh.

![Mesh VPN Tunnel](/drawings/services/meshflow.drawio.svg)

## Multi Hop (To be done)

https://www.procustodibus.com/blog/2022/06/multi-hop-wireguard/