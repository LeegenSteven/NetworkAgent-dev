# Setup GCP Environment



## Setup gcloud on your laptop

```
gcloud init --no-launch-browser
```

Setup the following environment variables. They are used throughout the setup docs.

```
export GOOGLE_PROJECT=free5gc-384814
export GOOGLE_REGION=europe-west2
export GOOGLE_ZONE=europe-west2-a
```

## Create service account

Run the following to create a new service account

```
gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/editor"
gcloud iam service-accounts keys create "./networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
```

## GKE Automation Platform

Create a __mgmt__ VPC to attach GKE and the edge appliances to. 

```
gcloud compute networks create mgmt --subnet-mode=custom
gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=europe-west2
gcloud compute firewall-rules create mgmt-ingress --network mgmt --allow tcp:8080,tcp:22,tcp:3389,tcp:443,icmp --direction INGRESS --source-ranges 0.0.0.0/0
```

Enable GKE APIs

```
gcloud services enable container.googleapis.com
```

Create GKE Cluster and install kubectl

```
gcloud container clusters create networkautomation \
    --release-channel stable \
    --addons ConfigConnector \
    --service-account $GOOGLE_SERVICE_ACCOUNT\
    --scopes default,storage-full,cloud-platform,bigquery \
    --workload-pool $GOOGLE_PROJECT.svc.id.goog \
    --logging SYSTEM \
    --monitoring SYSTEM \
    --zone europe-west2-a\
    --node-locations europe-west2-a \
    --num-nodes 5 \
    --network mgmt \
    --subnetwork mgmt-subnet
```

Install kubectl and get cluster credentials

```
gcloud components install kubectl
gcloud container clusters get-credentials networkautomation --region=$GOOGLE_REGION
```

Attach service account to config connector

```
gcloud iam service-accounts add-iam-policy-binding \
$GOOGLE_SERVICE_ACCOUNT \
    --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
    --role="roles/iam.workloadIdentityUser"
```

### Install Config Connector

Specify the GKE namespace and project for Config Connector to create resources in

```
cd NetworkAgent/environment
kubectl create namespace automation
kubectl annotate namespace automation cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
kubectl config set-context --current --namespace automation
kubectl apply -f configconnector.yaml
```

Verify the config connector installation

```
kubectl wait -n cnrm-system --for=condition=Ready pod --all
```

## Create SSH keys

To allow orchestration operators to log into the network virtual machines we create SSH keys and register with GCP.

```
cd NetworkAgent/environment
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C briannaughton
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=1d
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

[Install Porch and config sync by following these instructions](https://kpt.dev/guides/porch-installation)

```
kpt alpha repo register \
  --namespace automation \
  --repo-basic-username=<<username>> \
  --repo-basic-password=<<password>> \
  http://<<YOUR EXTERNAL IP>>/brian/test.git
```

## Docker repo

Create a docker repository to push our container images to.

```
gcloud artifacts repositories create networkagent --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository"
```

## Create Demo VPC Networks

Create the Base VPCs, Subnets and dummy IT apps for the demo.

```
cd NetworkAgent/environment/
kubectl apply -f customersites
```

## Deploy Network Service Operator

[Instructions here](/operator/Readme.md)

## Deploy Rest Tools

[Instructions here](/tools/Readme.md)

## Deploy Network Agent

[Instructions here](/networkagent/Readme.md)


