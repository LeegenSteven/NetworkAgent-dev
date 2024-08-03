# Edge Network Appliance Operator

This kubernetes operator manages the lifecycle of the following components:

* __WireguardAppliancee__: Wireguard based VPN appliance. 
* __ConnectivityService__: Logical VPN service. 
* __Monitor__: Observability of the end to end service and VyOS virtual machines. 

The operator is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [anisble playbooks](https://docs.ansible.com/).

## Build and deploy the Edge Network Appliance Operator

To build and push the edge appliance operator image, run the following commands

```
gcloud auth configure-docker europe-west2-docker.pkg.dev
```

To build locally and push, run the following commands from the __NetworkAgent/operator__ directory

```
docker build . -t europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkoperator:latest
docker push europe-west2-docker.pkg.dev/free5gc-384814/networkagent/networkoperator:latest
```

## Running the operator locally on your laptop

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
kopf run main.py --verbose
```

https://www.procustodibus.com/blog/2022/06/multi-hop-wireguard/

## References

* [ansible runner](https://ansible.readthedocs.io/projects/runner/en/latest/python_interface/)

## TODO

* Update status when lifecycle us complete on resource and service
* Wireguard: attach the config parameters to the spec/status and update the status field
* Service: refer to the children objects created?, i.e. implement hierarchy -> service owns the resource objects, and when service is delete everything else is auto deleted. 
* Ansible is not threaded or running in parallel
* Deploy to GKE and test credentials are working correctly - may need a service account