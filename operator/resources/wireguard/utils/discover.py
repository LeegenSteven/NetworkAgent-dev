import logging
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
            if self.external_ip_address is None:
                self.external_ip_address = data['event_data']['res']['resources'][0]['spec']['address']
                logger.info("found external ip address %s", self.external_ip_address)
            else:
                self.peer_ip_address = data['event_data']['res']['resources'][0]['spec']['address']            
                logger.info("found peer ip address %s", self.peer_ip_address)

    def get_vm_facts_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            self.vm_ansible_facts = data['event_data']['res']['ansible_facts']
            logger.info("got vm facts")

    def get_interface_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            logger.info("interface discover complete")
            self.interface_cidr = data['event_data']['res']['resources'][0]['spec']['ipCidrRange']
            logger.info("found interface cidr %s", self.interface_cidr)
