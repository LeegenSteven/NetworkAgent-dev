# Edge Network Appliance Operator

This kubernetes operator manages the lifecycle of the following components:

* __EdgeAppliance__: VyOS virtual machine. 
* __VPNService__: Logical VPN service. 
* __Monitor__: Observability of the end to end service and VyOS virtual machines. 

The operator is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [anisble playbooks](https://docs.ansible.com/) to configure virtual network components.

## Build and deploy the Edge Network Appliance Operator

To build and push the edge appliance operator image, run the following commands

```
cd NetworkAgent/operator
gcloud builds submit --region=europe-west1 --tag europe-west1-docker.pkg.dev/$GOOGLE_PROJECT/networkagent/edgeoperator:1.0
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