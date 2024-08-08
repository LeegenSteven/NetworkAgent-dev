# Setup GCP Environment

## Setup gcloud on your laptop

```
gcloud init --no-launch-browser
```

## GCP Project(s)

TBD - create projects and service accounts etc.

* GKE needs a service account with compute admin + artifact registry read
* Tools needs a service account with GKE Admin
* Operator needs a service account with GKE Admin

## GKE Automation Platform

First, create a __mgmt__ VPC to attach GKE and the edge appliances to. 

```
gcloud compute networks create mgmt --subnet-mode=custom
gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=europe-west2
gcloud compute firewall-rules create mgmt-ingress --network mgmt --allow tcp:8080,tcp:22,tcp:3389,tcp:443,icmp --direction INGRESS --source-ranges 0.0.0.0/0
```

Create GKE Cluster and install kubectl

```
gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --service-account free5gc-vm@free5gc-384814.iam.gserviceaccount.com\
    --scopes default,storage-full,cloud-platform,bigquery \
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

### Install Config Connector

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

## Create SSH keys for Virtual Machines

To allow orchestration operators to log into the network virtual machines we create SSH keys and register with GCP.

```
cd NetworkAgent/environment
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C briannaughton
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=free5gc-384814 --ttl=1d
```

## Setup git and config sync

Deploy git to GKE

```
kubectl apply -f git.yaml
```

To find the external IP assigned to git run the following command

```
kubectl get service gitea-lb-service --output yaml
```

You should see an external IP address under loadbalancer:ingress

```
spec:
  ...
  ports:
  - ...
    port: 60000
    protocol: TCP
    targetPort: 50001
  selector:
    app: products
    department: sales
  sessionAffinity: None
  type: LoadBalancer
status:
  loadBalancer:
    ingress:
    - ip: <<YOUR EXTERNAL IP>>
```

You can reach git at __http://<<YOUR EXTERNAL IP>>0:8080__


[setup manually](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/how-to/installing-kubectl#deploying)


## Docker repo

Create a docker repository to push our container images to.

```
gcloud artifacts repositories create networkagent --repository-format=docker --location=europe-west2 --description="Network Agent Repository"
```

## Create Demo VPC Networks

Create the Base VPCs, Subnets and dummy IT apps for the demo.

```
cd NetworkAgent/sample-service/customersites
kubectl apply -f london.yaml
kubectl apply -f newyork.yaml
kubectl apply -f singapore.yaml
kubectl apply -f sydney.yaml
```

## Deploy Network Service Operator

[Instructions here](/operator/Readme.md)

## Deploy Rest Tools

[Instructions here](/tools/Readme.md)

## Deploy Network Agent

[Instructions here](/networkagent/Readme.md)


