import kopf
import ansible_runner
import os
import logging
import kubernetes
import utils.constants as constants

logger = logging.getLogger(__name__)

class SubnetworkEvent:
    def __init__(self):
        self.cidr=None

    def handle_event(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            vm_k8s_object = data['event_data']['res']['resources'][0]['spec']
            self.cidr = vm_k8s_object['ipCidrRange']


def getSubnetInfo(pdir, subnet_event, vpc):

    logger.info("building service parameters")
    logger.info("getting %s vpc object", vpc)
    aendevent = SubnetworkEvent()    
    extravars = {'subnetname': vpc}

    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='subnet.yaml',
                           extravars=extravars,
                           event_handler=subnet_event.handle_event)
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if subnet_event.cidr is None:
        raise kopf.TemporaryError("No CIDR found", delay=15)

    logger.info('%s subnet cidr = %s', vpc, subnet_event.cidr)


@kopf.on.create('connectivityservice')
async def create_connectivityservice_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create connectivityservice service handler is called with spec: {spec}")

    cwd = os.getcwd()
    pdir = cwd+"/services/sitetosite/playbooks"
    logger.info("playbook dir = %s", pdir)

    if spec.get("type") != "site-to-site":
        raise kopf.PermanentError("Only site-to-site supported.")

    if len(spec.get('vpcs'))!=2:
        raise kopf.PermanentError("Only two VPCs are allowed.")

    aend=spec.get('vpcs')[0]
    bend=spec.get('vpcs')[1]

    logger.info("getting %s vpc object", aend)
    aendevent = SubnetworkEvent()
    getSubnetInfo(pdir, aendevent, aend)
    bendevent = SubnetworkEvent()
    getSubnetInfo(pdir, bendevent, bend)

    extravars = {
        'sitename': aend+'-external',
        'cidr': '10.0.50.0/24', 
        'project': constants.PROJECT,
        'region': constants.REGION,
        'zone': constants.ZONE,
        'mgmtsubnetname': 'mgmt-subnet',
        'vmname': aend+'-external',
        'interface': aend,
        'peerinterface': bend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.1',
        'peername': bend+'-external'
    }
    logger.info(extravars)

    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='createsite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

    # for each site run the following
    extravars = {
        'sitename': bend+'-external',
        'cidr': '10.0.60.0/24', 
        'project': constants.PROJECT,
        'region': constants.REGION,
        'zone': constants.ZONE,
        'mgmtsubnetname': 'mgmt-subnet',
        'vmname': bend+'-external',
        'interface': bend,
        'peerinterface': aend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.2',
        'peername': aend+'-external'
    }
    logger.info(extravars)
    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='createsite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)


@kopf.on.delete('connectivityservice')
async def delete_connectivityservice_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Delete connectivity service handler is called with spec: {spec}")

    cwd = os.getcwd()
    pdir = cwd+"/services/sitetosite/playbooks"
    logger.info("playbook dir = %s", pdir)

    aend=spec.get('vpcs')[0]
    bend=spec.get('vpcs')[1]

    extravars = {
        'sitename': aend+'-external',
        'cidr': '10.0.50.0/24', 
        'region': constants.REGION,
        'zone': constants.ZONE,
        'vmname': aend+'-external-vm',
        'interface': aend,
        'peerinterface': bend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.1',
        'peername': bend+'-external-vm'
    }
    logger.info(extravars)

    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='deletesite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

    # for each site run the following
    extravars = {
        'sitename': bend+'-external',
        'cidr': '10.0.60.0/24', 
        'region': constants.REGION,
        'zone': constants.ZONE,
        'vmname': bend+'-external-vm',
        'interface': bend,
        'peerinterface': aend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.2',
        'peername': aend+'-external-vm'
    }
    logger.info(extravars)
    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='deletesite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
