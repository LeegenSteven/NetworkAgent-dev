# TODO

Candidate todo list. Each task rated as:

* __H__: High
* __M__: Medium
* __L__: Low

## Environment

* __L__: Automate environment creation with Terraform/Ansible

## Operator

* __H__: generate wireguard keys for each edge rather than static key list? Attach maybe to configmaps?
* __H__: Add firewall rule to allow allowed traffic from allowedinterface and add route between customer VPC/location and edge VM to route traffic to allowed interfaces
* __M__: Run a test after VMs are up to test the tunnels are working - then mark service operational.

## Tools/Rest endpoint

* __H__: Hook up read/create service APIs
* __L__: Create Swagger spec for API that can be loaded into LLM
* __M__: Move back end for rest to config sync rather than direct to k8s

## Monitoring

* __H__: Create collection infra and pipeline into BQ/pubsub/?
* __H__: Create Monitoring CRD in operator to deploy agent to the edge VMs and hook up to pipeline above 
* __M__: Dashboards? Can we see the network hierarchy/topology at all and overlay performance metrics?

## Config Sync/Nephio Change Mgmt

* __M__: Create scripts/CRs to deploy git repo VM on GCE (GKE?)
* __M__: Setup configsync with webhooks to above git
* __M__: Blueprints? Figure out how to include the service/resource CRs in a blueprint package story
* __M__: Definitely make changes to cluster through this interface rather than direct to k8s

## Network Services

* __H__: Site to site: Get simplest 2 site vpn working end to end
* __M__: Hub and spoke: 
* __M__: Mesh: 

## Netbox

??

## Network Agent

* __H__: Query locations
* __H__: Query existing services
* __H__: Query available services
* __H__: Q&A to create new services
* __H__: How is service performing

## Network Tests

* __H__: Test CRD/Operator that logs onto customer VMs and installs/runs software to emulate web traffic or general iperf
* __L__: Chaos monkey like tests to throttle or break network interfaces