# TODO

Candidate todo list. Each task rated as:

* __H__: High
* __M__: Medium
* __L__: Low

## Environment

* __L__: Automate environment creation with Terraform/Ansible

## Operator

* __H__: Update status when lifecycle is complete on resource and service and better manage error reporting on CR
* __H__: generate wireguard keys for each edge rather than static key list? Attach maybe to configmaps
* __H__: Deploy to GKE and test credentials are working correctly - may need a service account
* __H__: Add firewall rule to allow allowed traffic from allowedinterface and add route between customer VPC/location and edge VM to route traffic to allowed interfaces
* __H__: Service: refer to the children objects created?
  * generate unique name for instance
  * initially tag each child metadata with service name
  * maybe implement parent-child hierarchy -> service owns the resource objects, and when service is delete everything else is auto deleted
* __M__: Run a test after VMs are up to test the tunnels are working - then mark service operational.
* __M__: Wireguard: attach the config parameters to the spec/status and update the status field
* __L__: Ansible is not threaded (all running sequentially)

## Tools/Rest endpoint

* __H__: Hook up read/create service APIs
* __L__: Create Swagger spec for API that can be loaded into LLM
* __M__: Move back end for rest to config sync rather than direc to k8s

## Monitoring

* __H__: Create collection infra and pipeline into BQ/pubsub/?
* __H__: Create Monitoring CRD in operator to deploy agent to the edge VMs and hook up to pipeline above 
* __M__: Dashboards? Can we see the network hierarchy/topology at all and overlay performance metrics?

## Config Sync/Nephio Change Mgmt

* __M__: Create scripts/CRs to deploy git repo VM on GCE (GKE?)
* __M__: Setup configsync with webhooks to above git
* __M__: Blueprints? Figure out how to include the service/resource CRs in a blueprint package story
* __M__: Definitely make changes to cluster through this interface rather than direct to k8s

## Services

* __H__: Site to site: Get simplest 2 site vpn working end to end
* __M__: Hub and spoke: 
* __M__: Mesh: 

## Network Agent

* __H__: Query locations
* __H__: Query existing services
* __H__: Query available services
* __H__: Q&A to create new services
* __H__: How is service performing

## Network Tests

* __H__: Test CRD/Operator that logs onto customer VMs and installs/runs software to emulate web traffic or general iperf
* __L__: Chaos monkey like tests to throttle or break network interfaces