# TODO

Candidate todo list. Each task rated as:

* __H__: High
* __M__: Medium
* __L__: Low

## Environment

* __L__: Automate environment creation with Terraform/Ansible

## Operator

* __H__: Add firewall rule to allow allowed traffic from allowedinterface and add route between customer VPC/location and edge VM to route traffic to allowed interfaces
* __H__: Create a CR that runs an iperf test between two VMs in customer locations
* __H__: Add monitoring agent to each VPN VM and connect to back end pipeline (discover details from config connector objects in k8s)
* __M__: move all k8s objects related to wireguard edge appliance under the wireguard object
* __M__: put VM certificate in one place/maybe in mounted configmap rather than in the container 
* __M__: create correct schemas for the VPN CRDs - can then be queried by the LLM dynamically
* __H__: generate wireguard keys for each edge rather than static key list
* __M__: Create a CR that runs a test job after VMs are up to test the tunnels are working - then mark service operational.

## Tools/Rest endpoint

* __H__: Convert from cloud run to run on GKE
* __H__: Hook up read/create service APIs with connectivity service object in GKE
* __L__: Create Swagger spec for API that can be loaded into LLM
* __M__: Move back end for creating new services to porch rather than direct to k8s

## Monitoring

* __H__: Create collection infra and pipeline into BQ/pubsub
* __M__: Dashboards? Can we see the network hierarchy/topology at all and overlay performance metrics?

## Config Sync/Nephio Change Mgmt

* __M__: Setup porch and configsync and register git instance
* __M__: Blueprints? Figure out how to include the service/resource CRs in a blueprint package story
* __M__: Definitely make changes to cluster through this interface rather than direct to k8s

## Network Services

* __H__: Site to site: Get simplest 2 site vpn working end to end
* __M__: Hub and spoke: 
* __M__: Mesh

## Netbox

* __M__: Deploy Netbox 
* __M__: Connect to ...

## Network Agent

* __H__: Query locations
* __H__: Query existing services
* __H__: Query available services and the info available
* __H__: Q&A to create new services
* __H__: How is service performing

## Network Tests

* __H__: Test CRD/Operator that logs onto customer VMs and installs/runs software to emulate web traffic or general iperf
* __L__: Chaos monkey like tests to throttle or break network interfaces