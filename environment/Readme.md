# Setup GCP Environment

```
gcloud init --no-launch-browser
```

## GCP Project(s)


## GKE Automation Platform

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
    --network=free5gc-demo 
```

Install kubectl and get cluster credentials

```
gcloud components install kubectl
gcloud container clusters get-credentials networkautomation --region=europe-west2-a
kubectl get namespaces
```

Attach service account to config connector

```
gcloud iam service-accounts add-iam-policy-binding \
free5gc-vm@free5gc-384814.iam.gserviceaccount.com \
    --member="serviceAccount:free5gc-384814.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
    --role="roles/iam.workloadIdentityUser"
```

Specify the project to create resources in

```
kubectl create namespace automation
kubectl annotate namespace automation cnrm.cloud.google.com/project-id=free5gc-384814
kubectl config set-context --current --namespace automation
kubectl apply -f configconnector.yaml
```

Check the config connector is running

```
kubectl wait -n cnrm-system --for=condition=Ready pod --all
```

## Create Demo VPC Networks

Create the VPCs and Subnets for the demo

```
kubectl apply -f demo-networks.yaml
```