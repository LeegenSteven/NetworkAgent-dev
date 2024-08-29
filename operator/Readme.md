# Network Operator

This kubernetes operator manages the lifecycle of the following components:

* __WireguardAppliancee__: Wireguard based VPN appliance. 
* __PointToPointService__: A VPN tunnel between two customer sites
* __Monitor__: A prometheus server that is updated to monitor whatever VPN virtual machines are created. 

The operator code is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [ansible playbooks](https://docs.ansible.com/) to run commands inside the network VMs.

