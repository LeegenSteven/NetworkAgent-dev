# LLM Tools

This folder contains a rest endpoint can be used by LLMs to interact with the Kubernetes network orchestration layer.


## Build and deploy docker image

Authenticate with the docker repo

```
gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev
```

To build and push run the following from the __NetworkAgent/tools__ directory

```
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networktools:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networktools:latest
```

## Deploy the tools service to Cloud Run

Run following to deploy the rest endpoint on cloud run

```
gcloud run deploy network-agent-api --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networktools:latest --region $GOOGLE_REGION --service-account $GOOGLE_SERVICE_ACCOUNT --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT,GOOGLE_REGION=$GOOGLE_REGION,GOOGLE_ZONE=$GOOGLE_ZONE
```

## Deploy the tools service to GKE

To run the network tools service run the following command

```
kubectl apply -f deployment.yaml
```

To find the external IP assigned to the network tools service run the following command

```
kubectl get service networktools-lb-service --output yaml
```

You should see an external IP address under __loadbalancer:ingress__

```
spec:
  ...
  ports:
  - ...
    port: 8080
    protocol: TCP
    targetPort: 8080
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

You can reach the network tools swagger endpoint at __http://<<YOUR EXTERNAL IP>>:8080/ui/ui__
