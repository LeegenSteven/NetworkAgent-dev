import kopf
import logging
import ansible_runner
import os
import pprint

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

class Events:
    def __init__(self):
        self.external_ip_name=None
        self.external_ip_address=None

    def get_vm_event_handler(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
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

@kopf.on.create('edgeappliances')
def create_fn(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    events=Events()

    edgevm = spec.get('edgevm')
    if not edgevm:
        raise kopf.PermanentError(f"edgevm must be set. Got {edgevm!r}.")

    cwd = os.getcwd()
    pdir = cwd+"/playbooks"
    logger.info("path = %s", pdir)

    # find the VM and get the name of the external computeaddress
    extravars = {'edge_vm_name': edgevm}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='getvm.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.external_ip_name is None:
        raise kopf.TemporaryError("No Edge VM found", delay=15)

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

    logger.info("about to run playbook on edgevm = %s", edgevm)
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
    r = ansible_runner.run(inventory={'all': hosts}, private_data_dir=pdir, playbook='setup.yaml')
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

# https://gist.github.com/privateip/879683a0172415c408fb2afb82a97511