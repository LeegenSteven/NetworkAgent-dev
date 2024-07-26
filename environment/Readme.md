# Setup GCP Environment

## Setup gcloud on your laptop

```
gcloud init --no-launch-browser
```

## GCP Project(s)

TBD - create projects and service accounts etc

## GKE Automation Platform

First, create a __mgmt__ VPC to attach GKE and the edge appliances to. 

```
gcloud compute networks create mgmt --subnet-mode=custom
gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=europe-west2
gcloud compute firewall-rules create mgmt-fw --network mgmt --allow tcp,udp,icmp --source-ranges 0.0.0.0/0
gcloud compute firewall-rules update mgmt-fw --allow tcp:22,tcp:3389,icmp
```

Create GKE Cluster and install kubectl

```
gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --workload-pool free5gc-384814.svc.id.goog \
    --logging SYSTEM \
    --monitoring SYSTEM \
    --zone europe-west2-a\
    --node-locations europe-west2-a \
    --num-nodes 4 \
    --network mgmt \
    --subnetwork mgmt-subnet
```

Install kubectl and get cluster credentials

```
gcloud components install kubectl
gcloud container clusters get-credentials networkautomation --region=europe-west2-a
```

Attach service account to config connector

```
gcloud iam service-accounts add-iam-policy-binding \
free5gc-vm@free5gc-384814.iam.gserviceaccount.com \
    --member="serviceAccount:free5gc-384814.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
    --role="roles/iam.workloadIdentityUser"
```

Specify the GKE namespace and project for Config Connector to create resources in

```
kubectl create namespace automation
kubectl annotate namespace automation cnrm.cloud.google.com/project-id=free5gc-384814
kubectl config set-context --current --namespace automation
kubectl apply -f configconnector.yaml
```

## Create Demo VPC Networks

Create the Base VPCs, Subnets and dummy IT apps for the demo.

```
kubectl apply -f demo-networks.yaml
kubectl apply -f apps.yaml
```
