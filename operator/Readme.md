# Edge Network Appliance Operator

This kubernetes operator manages the lifecycle of the following components:

* __WireguardAppliancee__: Wireguard based VPN appliance. 
* __ConnectivityService__: Logical VPN service. 
* __Monitor__: Observability of the end to end service and VyOS virtual machines. 

The operator is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [anisble playbooks](https://docs.ansible.com/).


## Build the Edge Network Appliance Operator

To build and push the edge appliance operator image, run the following commands

```
gcloud auth configure-docker europe-west2-docker.pkg.dev
```

To build locally and push, run the following commands from the __NetworkAgent/operator__ directory

```
docker build . -t europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkoperator:latest
docker push europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkoperator:latest
```

## Deploy the CRDs & Operator

Deploy the appliance and service CRDs.

```
cd NetworkAgent/operator/config
kubectl apply -f wireguard.yaml
kubectl apply -f service.yaml
```

Update the operator deployment manifest with your PROJECT, REGION and ZONE details as follows: 

```
  containers:
  - name: networkoperator
    image: europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkoperator:latest
    imagePullPolicy: Always
    env:
    - name: PROJECT
      value: <<YOUR PROJECT>>
    - name: REGION
      value: <<YOUR REGION>>
    - name: ZONE
      value: <<YOUR ZONE>>
```

Deploy the operator

```
cd NetworkAgent/operator
kubectl apply -f deployment.yaml
```

## Running the operator locally on your laptop

Set the following environment variables

```
export PROJECT=free5gc-384814
export REGION=europe-west2
export ZONE=europe-west2-a
```

Run the following to start the operator

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd src
kopf run main.py --verbose
```

https://www.procustodibus.com/blog/2022/06/multi-hop-wireguard/

## References

* [ansible runner](https://ansible.readthedocs.io/projects/runner/en/latest/python_interface/)

