# Build the Network Operator

To build and push the network operator image, run the following commands in the __NetworkAgent/operator__ directory

```
gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev
```

To build locally and push, run the following commands from the __NetworkAgent/operator__ directory

```
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkoperator:latest
```

# Deploy the CRDs & Operator

Deploy the resource and service CRDs. In the __NetworkAgent/operator__ directory run the following commands

```
kubectl apply -f config
```

Update the __deployment.yaml__ manifest with your PROJECT, REGION and ZONE details as follows: 

```
  containers:
  - name: networkoperator
    image: <YOUR REGION>-docker.pkg.dev/<YOUR PROJECT>/networkagent/networkoperator:latest
    imagePullPolicy: Always
    env:
    - name: PROJECT
      value: <<YOUR PROJECT>>
    - name: REGION
      value: <<YOUR REGION>>
    - name: ZONE
      value: <<YOUR ZONE>>
```

Deploy the operator, from the __NetworkAgent/operator__ directory run the following commands. 

```
kubectl apply -f deployment.yaml
```
