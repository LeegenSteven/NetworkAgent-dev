# Edge Network Appliance Operator

This kubernetes operator manages the lifecycle of the following components:

* __EdgeAppliance__: VyOS virtual machine. 
* __VPNService__: Logical VPN service. 
* __Monitor__: Observability of the end to end service and VyOS virtual machines. 

The operator is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [anisble playbooks](https://docs.ansible.com/) to configure VyOS components.

## Build and deploy the Edge Network Appliance Operator


## Running the operator locally on your laptop

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
kopf run main.py --verbose
```

## References


* [ansible runner](https://ansible.readthedocs.io/projects/runner/en/latest/python_interface/)