# Network Agent

## Build and deploy the agent docker image

```
cd NetworkAgent/networkagent
docker build . -t europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkagent:latest
docker push europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkagent:latest
```

To deploy run the following command

```
kubectl apply -f deployment.yaml
```

To find the external IP assigned to the network agent service run the following command

```
kubectl get service networkagent-lb-service --output yaml
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

You can reach the network agent at __http://<<YOUR EXTERNAL IP>>0:8080__


## Running the Agent on your laptop

```
python3 main.py
```
