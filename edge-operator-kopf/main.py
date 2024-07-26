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
        self.mgmt_ip=None

    def get_vm_details_event_handler(self, data):
        # logger.info("event caught for get vm details")
        if 'event' in data and data['event']=='runner_on_ok':
            # pp=pprint.PrettyPrinter(indent=4)
            # pp.pprint(data['event_data']['res']['resources'][0]['spec']['networkInterface'])
            interfaces = data['event_data']['res']['resources'][0]['spec']['networkInterface']
            for int in interfaces:
                if 'mgmt' in int['networkRef']['external']:
                    mgmt_ip=int['networkIpRef']['external']
                    logger.info("found mgmt_ip %s", mgmt_ip)
                    self.mgmt_ip=mgmt_ip
                    break

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

    r = ansible_runner.run(private_data_dir=pdir, playbook='getvmdetails.yaml', event_handler=events.get_vm_details_event_handler)
    logger.info("status = %s", r.status)
    logger.info("status = %s", r.stdout)

    logger.info("running playbook on edgevm = %s", edgevm)

    hosts = {
        'hosts': {
            'edgevm': {
                'ansible_host': events.mgmt_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': 'google-compute'
            }
        }
    }
    logger.info(hosts)
    r = ansible_runner.run(inventory={'all': hosts}, private_data_dir=pdir, playbook='install.yaml')#, event_handler=my_event_handler)

# https://gist.github.com/privateip/879683a0172415c408fb2afb82a97511