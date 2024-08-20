# Setup GCP Environment

This section describes how to set up a base Network Agent demo environment. 

## Setup gcloud on your laptop

[Install](https://cloud.google.com/sdk/docs/install) and initialise gcloud as follows:

```
gcloud init --no-launch-browser
```

## Setup GCP environment variables

Setup the following environment variables. They are used throughout the rest of the setup docs.

```
export GOOGLE_PROJECT=<YOUR PROJECT>
export GOOGLE_REGION=<YOUR REGION>
export GOOGLE_ZONE=<YOUR ZONE>
export GOOGLE_USER=<YOUR GCP PROJECT USERNAME> 
```

## Installation script setup

The __install.sh__ script in the root directory automates the network agent setup tasks. The script options are as follows:

```
./install.sh
Network Agent environment manager.

Syntax: scriptTemplate [-c|-s|-d]
options:
  -c     create manifests.
  -s     start network agent environment.
  -d     delete the network agent environment.
```

The first step is to generate the various keys and deployment descriptors for each of the network agent components by running the following command. 

```
./install.sh -c
```

The run the following command to start the various GCP services. 

```
./install.sh -s
```

Finally, to clean up, run the following command to remove the GCP services. 

```
./install.sh -d
```

## Manual network agent environment setup

The following sections describe how to manually stand up the environment. 

### Create SSH keys

To allow the network operator to log into the demo virtual machines create SSH keys and register with GCP. From the __NetworkAgent__ directory run the following commands. 

```
ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent
gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=5d
```

You must copy __google-compute__ and __google-compute.pub__ files to __NetworkAgent/operator/__ directory to allow the network operator to log into the VMs.


### Create service account

Run the following commands form the __NetworkAgent__ to create a new network agent service account.

```
gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter=name:"networkagent@"`
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/owner"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/container.admin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.admin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/compute.networkAdmin"
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/iam.serviceAccountAdmin"
gcloud iam service-accounts keys create "./networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
```

You must copy the __networkagent.json__ file to __NetworkAgent/tools__, __NetworkAgent/operator__ and __NetworkAgent/networkagent__ directories to allow the tools server and genai agent to access APIs.

### Generate Manifest files

Generate k8s manifest files used throughout the demo. In the __NetworkAgent__ directory, run the following commands. 

```
./install -c
```

### Create GKE Automation Platform

Create a __mgmt__ VPC to attach GKE and the edge appliances to. 

```
gcloud compute networks create mgmt --subnet-mode=custom
gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
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
    --zone $GOOGLE_ZONE\
    --node-locations $GOOGLE_ZONE \
    --num-nodes 5 \
    --network mgmt \
    --subnetwork mgmt-subnet
```

Install kubectl and get cluster credentials

```
gcloud components install kubectl
gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE
```

#### Install Config Connector

First, attach the service account to config connector

```
gcloud iam service-accounts add-iam-policy-binding \
$GOOGLE_SERVICE_ACCOUNT \
    --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
    --role="roles/iam.workloadIdentityUser"
```

In the __NetworkAgent/environment__ run the following commands to start config connector in the __automation__ namespace

```
kubectl create namespace automation
kubectl annotate namespace automation cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
kubectl config set-context --current --namespace automation
kubectl apply -f configconnector.yaml
```

Verify the config connector installation is ready with the following command

```
kubectl wait -n cnrm-system --for=condition=Ready pod --all
```

### Create Docker repo

Create a docker repository to push network agent container images to.

```
gcloud artifacts repositories create networkagent --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository"
```

### Setup Big query & PubSub subscription

Find your project number from the project details dashboard and add the following principal in IAM to allow pubsub accecss to your project.

```
service-<YOUR PROJECT ID NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com
```

In the __NetworkAgent/environment__ directory, run the following command to create the topic and big query pubsub subscription.

```
kubectl apply -f monitoring.yaml
```

At the moment need to go to pubsub subscription dashboard and delete the service performance subscription. It will be recreated correctly.

### Create Customer Locations

Create the base customer VPCs, Subnets and dummy IT apps for the demo. Run the following command from the __NetworkAgent/sample-services__ directory.

```
kubectl apply -f customersites
```

### Setup git and config sync (TBD)

To deploy git to GKE run the following command from the __NetworkAgent/environment__ directory.

```
kubectl apply -f git.yaml
```

To find the external IP assigned to git run the following command

```
kubectl get service gitea-lb-service --output yaml
```

After a couple of minutes, you should see an external IP address under loadbalancer:ingress

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

