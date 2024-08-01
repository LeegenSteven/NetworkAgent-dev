import kopf
import ansible_runner
import os
import logging
from resources.wireguard.utils.discover import WireguardEvents
import asyncio

# https://wireguard.how/server/google-cloud-platform/
# https://ubuntu.com/server/docs/wireguard-vpn-site-to-site

logger = logging.getLogger(__name__)

@kopf.on.create('wireguardappliance')
async def create_wireguard_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    events=WireguardEvents()

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    cwd = os.getcwd()
    pdir = cwd+"/resources/wireguard/playbooks"
    logger.info("path = %s", pdir)

    # find the VM k8s object
    extravars = {'vmname': vmname}
    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='vminfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.vm_k8s_object is None:
        raise kopf.TemporaryError("No Wireguard VM found", delay=15)

    # get the public ip address of the VM on the computeaddress object
    extravars = {'edge_ip_name': events.external_ip_name}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='getaddress.yaml', 
                           extravars=extravars,
                           event_handler=events.get_address_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.external_ip_address is None:
        raise kopf.TemporaryError("No Edge External IP address found", delay=15)

    # # find the VM ansible facts
    # hosts = {
    #     'hosts': {
    #         'edgevm': {
    #             'ansible_host': events.external_ip_address,
    #             'ansible_user': 'admin_briannaughton_altostrat_co',
    #             'ansible_connection': 'ssh',
    #             'ansible_ssh_private_key_file': 'google-compute',
    #             'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
    #         }
    #     }
    # }
    # r = ansible_runner.run(private_data_dir=pdir, 
    #                        inventory={'all': hosts},
    #                        playbook='vmfacts.yaml',
    #                        extravars=extravars,
    #                        event_handler=events.get_vm_facts_handler)

    # logger.info("status = %s", r.status)
    # if r.status != 'successful':
    #     raise kopf.TemporaryError("Ansible Error.", delay=15)
    # if events.vm_ansible_facts is None:
    #     raise kopf.TemporaryError("No VM facts", delay=15)

    # find the allowed interface
    extravars = {'interface': spec.get('allowedInterface')}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='interfaceinfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_interface_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.interface_cidr is None:
        raise kopf.TemporaryError("No Interface found", delay=15)

    # find the peer k8s object
    extravars = {'vmname': spec.get('peer')}
    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='vminfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.peer_ip_name is None or events.vm_k8s_object is None:
        raise kopf.TemporaryError("No peer found", delay=15)

    # get the public ip address of the VM on the computeaddress object
    extravars = {'edge_ip_name': events.peer_ip_name}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='getaddress.yaml', 
                           extravars=extravars,
                           event_handler=events.get_address_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.peer_ip_address is None:
        raise kopf.TemporaryError("No Edge External IP address found", delay=15)

    extravars = {
        'tunnel_address': spec.get('tunnelAddress'),
        'tunnel_cidr': spec.get('tunnelSubnet'),
        'default_interface': 'ens5' , 
        'interface_cidr' : events.interface_cidr,
        'peer_name': spec.get('peer'),
        'peer_ip_address' : events.peer_ip_address
    }

    # Install and configure wireguard
    hosts = {
        'hosts': {
            spec.get('vmname'): {
                'ansible_host': events.external_ip_address,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(hosts)
    logger.info(extravars)
    r = ansible_runner.run(private_data_dir=pdir, 
                           inventory={'all': hosts},
                           playbook='install.yaml',
                           extravars=extravars)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

    return {"status": "running"}

@kopf.on.update('wireguardappliance')
def update_wireguard_instance(spec, name, namespace, logger, **kwargs):
    logger.info("update called")


@kopf.on.delete('wireguardappliance')
def delete_wireguard_instances(spec, **_):
    logger.info("delete called")