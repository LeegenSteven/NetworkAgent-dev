import kopf
import ansible_runner
import os
import logging

logger = logging.getLogger(__name__)

class WireguardEvents:
    def __init__(self):
        self.external_ip_name = None
        self.external_ip_address = None
        self.vm_k8s_object = None
        self.vm_ansible_facts = None

    def get_vm_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            self.vm_k8s_object = data['event_data']['res']['resources'][0]['spec']
            # get the external ip address of the vm    
            interfaces = data['event_data']['res']['resources'][0]['spec']['networkInterface']
            for int in interfaces:
                if 'accessConfig' in int:
                    self.external_ip_name=int['accessConfig'][0]['natIpRef']['name']
                    logger.info("found external_ip_name %s", self.external_ip_name)
                    break

    def get_address_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            self.external_ip_address = data['event_data']['res']['resources'][0]['spec']['address']
            logger.info("found external ip address %s", self.external_ip_address)

    def get_vm_facts_handler(self, data):
        logger.info(data)

    def get_interface_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            logger.info("interface discover complete")
            logger.info(data)

    def get_peer_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            logger.info("peer discover complete")
            logger.info(data)

@kopf.on.create('wireguardappliance')
def create_edge_appliances(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    events=WireguardEvents()

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    cwd = os.getcwd()
    pdir = cwd+"/wireguard/playbooks"
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

    # find the VM ansible facts
    hosts = {
        'hosts': {
            'edgevm': {
                'ansible_host': events.external_ip_address,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute'
            }
        }
    }
    r = ansible_runner.run(private_data_dir=pdir, 
                           inventory={'all': hosts},
                           playbook='vmfacts.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_facts_handler)

    # logger.info("status = %s", r.status)
    # if r.status != 'successful':
    #     raise kopf.TemporaryError("Ansible Error.", delay=15)
    # if events.vm_k8s_object is None:
    #     raise kopf.TemporaryError("No Wireguard VM found", delay=15)


    # # find the interface facts
    # extravars = {'interface': spec.get('interface')}
    # r = ansible_runner.run(private_data_dir=pdir, 
    #                        playbook='interfaceinfo.yaml',
    #                        extravars=extravars,
    #                        event_handler=events.get_interface_event_handler)

    # logger.info("status = %s", r.status)
    # if r.status != 'successful':
    #     raise kopf.TemporaryError("Ansible Error.", delay=15)
    # # if events.interface_facts is None or events.vm_k8s_object is None:
    # #     raise kopf.TemporaryError("No Interface found", delay=15)

    # # find the peer facts
    # extravars = {'peer': spec.get('peer')}
    # r = ansible_runner.run(private_data_dir=pdir,
    #                        playbook='peerinfo.yaml',
    #                        extravars=extravars,
    #                        event_handler=events.get_peer_event_handler)

    # logger.info("status = %s", r.status)
    # if r.status != 'successful':
    #     raise kopf.TemporaryError("Ansible Error.", delay=15)
    # # if events.interface_facts is None or events.vm_k8s_object is None:
    # #     raise kopf.TemporaryError("No Interface found", delay=15)

    return {"status": "ok"}

@kopf.on.update('wireguardappliance')
def update_edge_appliances(spec, name, namespace, logger, **kwargs):
    logger.info("update called")


@kopf.on.delete('wireguardappliance')
def delete_edge_appliances(spec, **_):
    logger.info("delete called")