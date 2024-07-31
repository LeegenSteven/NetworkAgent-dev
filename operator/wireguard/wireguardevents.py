import kopf
import ansible_runner
import os
import logging

logger = logging.getLogger(__name__)

class WireguardEvents:
    def __init__(self):
        pass

    def get_vm_event_handler(self, data):
        pass

    def get_interface_event_handler(self, data):
        pass

    def get_peer_event_handler(self, data):
        pass

@kopf.on.create('wireguardappliance')
def create_edge_appliances(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    events=WireguardEvents()

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    cwd = os.getcwd()
    pdir = cwd+"/playbooks"
    logger.info("path = %s", pdir)

    # find the VM and get the name of the external computeaddress
    extravars = {'vmname': vmname}
    r = ansible_runner.run(private_data_dir=pdir, 
                           playbook='vminfo.yaml',
                           extravars=extravars,
                           event_handler=events.get_vm_event_handler)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if events.external_ip_name is None:
        raise kopf.TemporaryError("No Wireguard VM found", delay=15)

@kopf.on.update('wireguardappliance')
def update_edge_appliances(spec, name, namespace, logger, **kwargs):
    logger.info("update called")


@kopf.on.delete('wireguardappliance')
def delete_edge_appliances(spec, **_):
    logger.info("delete called")