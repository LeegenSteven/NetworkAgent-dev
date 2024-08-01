# Network Agent

## Setup gcloud CLI

Install the [gcloud cli tools](https://cloud.google.com/sdk/gcloud). Then run the following comands in a terminal to configure your google project and target region.

```
export GOOGLE_PROJECT=XXXXX
gcloud auth login --no-launch-browser
gcloud config set project $GOOGLE_PROJECT
gcloud config set compute/region "europe-west2"
gcloud config set compute/zone "europe-west2-b"
```

## Create service account credentials

Need to create service account and add credentials to network agent

```
cd NetworkAgent/agent
gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="networkagent account"
export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)"  --filter=name:"networkagent@"`
gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" --role="roles/editor"
gcloud iam service-accounts keys create "./networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
```

## Build and deploy the docker image

```
gcloud builds submit --region=europe-west1 --tag europe-west1-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkagent:1.0
```

## Running the Agent on your laptop

```
export KUBECONFIG
export ...
python3 main.py
```

## Running the Agent on GCP

Deploy to k8s cluster...