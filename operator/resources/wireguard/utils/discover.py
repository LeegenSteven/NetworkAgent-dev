import logging
import kopf
import ansible_runner

logger = logging.getLogger(__name__)

class WireguardEvents:
    def __init__(self):
        self.external_ip_name = None
        self.external_ip_address = None
        self.vm_k8s_object = None
        self.vm_ansible_facts = None
        self.interface_cidr = None
        self.peer_ip_name = None
        self.peer_ip = None

    def get_vm_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            # if no result then wait
            if len(data['event_data']['res']['resources']) == 0:
                raise kopf.TemporaryError("No VM object found", delay=15)

            self.vm_k8s_object = data['event_data']['res']['resources'][0]['spec']
            # get the external ip address of the vm    
            interfaces = data['event_data']['res']['resources'][0]['spec']['networkInterface']
            for int in interfaces:
                if 'accessConfig' in int:
                    if self.external_ip_name is None:
                        self.external_ip_name=int['accessConfig'][0]['natIpRef']['name']
                        logger.info("found external_ip_name %s", self.external_ip_name)
                    else:
                        self.peer_ip_name = int['accessConfig'][0]['natIpRef']['name']
                        logger.info("found peer_ip_name %s", self.peer_ip_name)
                    break

    def get_address_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':

            # if no result then wait
            if len(data['event_data']['res']['resources']) == 0:
                raise kopf.TemporaryError("No Address found", delay=15)

            if self.external_ip_address is None:
                self.external_ip_address = data['event_data']['res']['resources'][0]['spec']['address']
                logger.info("found external ip address %s", self.external_ip_address)
            else:
                self.peer_ip_address = data['event_data']['res']['resources'][0]['spec']['address']            
                logger.info("found peer ip address %s", self.peer_ip_address)

    def get_vm_facts_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':

            # if no result then wait
            if len(data['event_data']['res']['resources']) == 0:
                raise kopf.TemporaryError("No VM found", delay=15)

            self.vm_ansible_facts = data['event_data']['res']['ansible_facts']
            logger.info("got vm facts")

    def get_interface_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            logger.info("interface discover complete")

            # if no result then wait
            if len(data['event_data']['res']['resources']) == 0:
                raise kopf.TemporaryError("No subnet found", delay=15)

            self.interface_cidr = data['event_data']['res']['resources'][0]['spec']['ipCidrRange']
            logger.info("found interface cidr %s", self.interface_cidr)

def get_vm_object(pdir, events, vmname):
    logger.info("getting VM object")

    extravars = {'vmname': vmname}
    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='vminfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Failed.", delay=15)
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
        raise kopf.TemporaryError("Ansible Failed.", delay=15)
    if events.external_ip_address is None:
        raise kopf.TemporaryError("No Edge External IP address found", delay=15)


def get_vm_facts(pdir, events):
    logger.info("getting VM facts")

    # find the VM ansible facts
    hosts = {
        'hosts': {
            'edgevm': {
                'ansible_host': events.external_ip_address,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    r = ansible_runner.run(private_data_dir=pdir, 
                           inventory={'all': hosts},
                           playbook='vmfacts.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_facts_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.vm_ansible_facts is None:
        raise kopf.TemporaryError("No VM facts", delay=15)

def get_allowed_interface(pdir, events, interface):
    logger.info("get allowed interface")
    extravars = {'interface': interface}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='interfaceinfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_interface_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.interface_cidr is None:
        raise kopf.TemporaryError("No Interface found", delay=15)


def get_oeer_object(pdir, events, peername):
    logger.info("get peer object")

    extravars = {'vmname': }
    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='vminfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.peer_ip_name is None or events.vm_k8s_object is None:
        raise kopf.TemporaryError("No peer found", delay=15)


def get_public_ip(pdir, events):
    logger.info("getting public ip address")
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


def install(pdir, events, spec ):
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
