import logging
import kubernetes
import kopf
import ansible_runner
import utils.constants as constants
import os

logger = logging.getLogger(__name__)

#####################################################################
# Get Compute Instance Info
#####################################################################
async def get_vm_info(vmname):
    logger.info("get info for vm %s", vmname)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeInstance",
    )

    try:
        result = network_api.get(name=vmname, namespace="automation")
        status = result.get('status')
        if status.get('currentStatus') != "RUNNING":
            raise kopf.TemporaryError("Waiting for VM to come up")
        return result
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No VM {vmname} found yet")

#####################################################################
# Get Compute Address Info
#####################################################################
async def get_address_info(addressname):
    logger.info("get info for address %s", addressname)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeAddress",
    )

    try:
        result = network_api.get(name=addressname, namespace="automation")
        conditions = result.get('status').get('conditions')
        if conditions[-1].get('reason') != "UpToDate":
            raise kopf.TemporaryError("Waiting for address to come up")
        return result
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No address {addressname} found yet")

#####################################################################
# Get Compute Subnet Info
#####################################################################
async def get_subnet_info(subnetname):
    logger.info("get info for subnet %s", subnetname)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeSubnetwork",
    )

    try:
        result = network_api.get(name=subnetname, namespace="automation")
        conditions = result.get('status').get('conditions')
        if conditions[-1].get('reason') != "UpToDate":
            raise kopf.TemporaryError("Waiting for subnet to come up")
        return result
    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No subnet {subnetname} found yet")

#####################################################################
# Install VPN software on VM
#####################################################################
async def install_vpn(vmname, external_ip_address, tunnel_address, tunnel_cidr, interface_cidr, peer_name, peer_ip_address, keys, peer_keys):
    logger.info("Install VPN")

    extravars = {
        'tunnel_address': tunnel_address,
        'tunnel_cidr': tunnel_cidr,
        'default_interface': 'ens5' ,
        'interface_cidr' : interface_cidr,
        'peer_name': peer_name,
        'peer_ip_address' : peer_ip_address,
        'keys': keys, 
        'peer_keys': peer_keys,
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir
    }
    hosts = {
        'hosts': {
            vmname: {
                'ansible_host': external_ip_address,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(hosts)
    logger.info(extravars)
    r = ansible_runner.run(private_data_dir=constants.basedir+"/resources/wireguard/playbooks", 
                           inventory={'all': hosts},
                           playbook='install.yaml',
                           extravars=extravars)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
