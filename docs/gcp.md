# GCP Environment

A single GCP project is used to deploy the entire demo, orchestrating the customer environment and the connectivity services into an operational state. 

![Final environment](/drawings/simple-service.drawio.svg)

The figure above shows a GCP project for a simple site to site service connecting IT applications across 4 private VPC locations spread around the globe. 

The demo environment orchestrates the following components into planes:

* __Customer VPCs__: The customer brings their own VPCs
* __Mgmt and dataplane VPCs__: A management VPC carries all orchestration and monitoring metrics, and a dataplane VPC carries all VPN traffic. 
* __Prometheus Server__: A prometheus server is deployed on the management network and runs queries against Edge appliance node exporters. 
* __Wireguard Edge Appliances__: VPN virtual appliances are deployed and connected to customer and dataplane networks to create one or more tunnels.

