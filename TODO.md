# TODO

Todo list. Each task rated as:

* __H__: High
* __M__: Medium
* __L__: Low

## Operator
* __H__: Install Ops Agent. curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
sudo bash add-google-cloud-ops-agent-repo.sh --also-install

## Graph

* __H__: Add metrics to graph or at least use graph to make sense of BQ metrics

## Tools/Rest endpoint

* __H__: Update the service performance endpoint to either query prometheus directly in the near term or whatever the new BQ model becomes
* __M__: Move back end for creating new services to porch rather than direct to k8s

## Monitoring

* __H__: rewrite publish python script to query prometheus server and send metrics to BQ
* __H__: Enable VPC flows


## Config Sync/Nephio Change Mgmt

* __M__: Setup porch and configsync and register git instance
* __M__: Blueprints? Figure out how to include the service/resource CRs in a blueprint package story
* __M__: Automate pipeline from LLM to deployment

## Network Services

* __M__: Add Hub and spoke: 

## Netbox

* __M__: Use Netbox to visualise/plan?

## Network Agent

* __H__: Make Gemini work with function calling - currently there are out of order calls being flagged

## Network Tests

* __L__: Add Web traffic simulation
* __L__: Add Probe tests?