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
gcloud compute firewall-rules create mgmt-ingress --network mgmt --allow tcp:22,tcp:3389,tcp:443,icmp --direction INGRESS --source-ranges 0.0.0.0/0
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
cd NetworkAgent/environment
kubectl create namespace automation
kubectl annotate namespace automation cnrm.cloud.google.com/project-id=free5gc-384814
kubectl config set-context --current --namespace automation
kubectl apply -f configconnector.yaml
```

Verify the config connector installation

```
kubectl wait -n cnrm-system --for=condition=Ready pod --all
```

## Setup config sync

[setup manually](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/how-to/installing-kubectl#deploying)

## Create Demo VPC Networks

Create the Base VPCs, Subnets and dummy IT apps for the demo.

```
cd NetworkAgent/environment
kubectl apply -f demo-networks.yaml
kubectl apply -f dummy-apps.yaml
```


## Create SSH keys 

To allow orchestration operators to log into the network virtual machines we create SSH keys and register with GCP.

```
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C briannaughton
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=free5gc-384814 --ttl=1d
```

## Docker repo

Create a docker repository to push our Pathway Gateway container image to.

```
gcloud artifacts repositories create networkagent --repository-format=docker --location=europe-west2 --description="Network Agent Repository"
```

