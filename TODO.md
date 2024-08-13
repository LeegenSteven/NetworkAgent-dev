# TODO

Candidate todo list. Each task rated as:

* __H__: High
* __M__: Medium
* __L__: Low

## Environment

* __L__: Automate environment creation with Terraform/Ansible

## Operator

* __H__: Create a CR that runs an iperf test between two VMs in customer locations
* __H__: Add monitoring agent to each VPN VM and connect to back end pipeline (discover details from config connector objects in k8s) https://thepythoncode.com/article/make-a-network-usage-monitor-in-python?utm_content=cmp-true
* __M__: Validate the networks exist, and do permanent fail if not
* __H__: use envFrom to seed container google environment variables
* __M__: move compute k8s objects related to wireguard edge appliance under the wireguard object
* __M__: put VM certificate in one place/maybe in mounted configmap rather than in the container 

## Tools/Rest endpoint

* __M__: post and delete for running a test across two apps
* __M__: find all apps running on each customer network, to figure out which vms to run tests on
* __L__: use Kustomizer/envFrom to seed container google environment variables and image name
* __M__: Move back end for creating new services to porch rather than direct to k8s

## Monitoring

* __H__: Create monitoring infra and pipeline into BQ/pubsub
* __M__: Dashboards? Can we see the network hierarchy/topology at all and overlay performance metrics?

## Config Sync/Nephio Change Mgmt

* __M__: Setup porch and configsync and register git instance
* __M__: Blueprints? Figure out how to include the service/resource CRs in a blueprint package story
* __M__: Automate pipeline from LLM to deployment

## Network Services

* __H__: Site to site: Get simplest 2 site vpn working end to end
* __M__: Hub and spoke: 
* __M__: Mesh

## Netbox

* __M__: Use Netbox to visualise maybe?

## Network Agent

* __H__: use envFrom or maybe Kustomize to seed container google environment variables and image name
* __H__: Move from gradio to streamlit https://medium.com/@jedrzejplucinski/4-steps-to-create-chat-bot-for-your-api-e563c897ef85
* __H__: Connect agent to internal tools service - no need for external IP for tools
* __H__: Q&A to collect all info needed to create new services
* __H__: How is service performing
* __H__: What tests are available
* __H__: Run/Stop a test

## Network Tests

* __H__: Test CRD/Operator that logs onto customer VMs and installs/runs software to emulate web traffic or general iperf
