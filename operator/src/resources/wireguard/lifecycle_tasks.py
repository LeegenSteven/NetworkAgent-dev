import logging
import kubernetes
import kopf
import ansible_runner
import utils.constants as constants
import os

logger = logging.getLogger(__name__)


#####################################################################
# Install VPN software on VM
#####################################################################
async def install_vpn(servicename, vmname, mgmt_ip_address, data_ip_address, tunnel_address, tunnel_cidr, keys, peers):
    logger.info("Install VPN")

    extravars = {
        'servicename': servicename,
        'data_ip_address': data_ip_address,
        'tunnel_address': tunnel_address,
        'tunnel_cidr': tunnel_cidr,
        'default_interface': 'ens6' ,
        'peers' : peers,
        'keys': keys, 
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir
    }
    hosts = {
        'hosts': {
            vmname: {
                'ansible_host': mgmt_ip_address,
                'ansible_user': os.getenv("GOOGLE_USER"),
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
        raise kopf.TemporaryError("Waiting for VM to come up.", delay=20)
