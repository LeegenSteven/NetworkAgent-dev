# Build and network agent docker image

In the __NetworkAgent/networkagent__ directory, run the following commands. 

```
docker build . -t $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkagent:latest
docker push $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/networkagent:latest
```

To deploy the network agent run the following command

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

You can reach the network agent at __http://<<YOUR EXTERNAL IP>>>:8080__


## Running the Agent on your laptop

Setup ADC to impersonate the service account bound to GKE workload identity. 

```
gcloud auth application-default login --impersonate-service-account $GOOGLE_SERVICE_ACCOUNT
```

Ensure the GOOGLE_PROJECT, GOOGLE_REGION and GOOGLE_ZONE environment variables are set (as described in the initial GCP setup readme)

To run the agent run the following:

```
python3 main.py
```

In VSCode you can set environment variables in your __launch.json__ file as below. 

```
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python Debugger: Current File",
            "type": "debugpy",
            "request": "launch",
            "program": "${file}",
            "console": "integratedTerminal",
            "env": {
                "GOOGLE_PROJECT": "free5gc-384814",
                "GOOGLE_REGION": "europe-west2",
                "GOOGLE_ZONE": "europe-west2-a",
                "NETWORK_TOOLS_URL": "http://35.189.94.231:8080",
                "NETWORK_AGENT_FILE": "./environment/networkagent.json"
            }
        }
    ]
}
```