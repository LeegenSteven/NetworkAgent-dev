# Lifecycle Management

The diagram below depicts the main components responsible for lifecycle management of the demo connectivity services. 

![lifecycle mgmt architecture](/drawings/lifecycle/lifecycle.drawio.svg)

Cloud network/compute resources, VNFs, observability virtual machines and cloud services are created and configured with a set of Kubernetes custom resource operators. 

## Intent based Automation

![service-resource crds](/drawings/lifecycle/service-resource.drawio.svg)

Any cloud compute infrastructure changes needed to deploy the VNF, create VPCs, subnetworks or update VPC routes are managed with the [GCP Config Connector](https://cloud.google.com/config-connector/docs/).

## Active Topology

![topology](/drawings/graph/relationships.drawio.svg)

![entity diagram](/drawings/graph/entity.drawio.svg)

## Monitoring

Observability CRDs - Prometheus and Grafana server

* [Prometheus](/docs/monitor.md)
* VPC Flows