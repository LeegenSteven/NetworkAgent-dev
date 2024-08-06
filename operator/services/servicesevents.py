import kopf
import ansible_runner
import os
import logging
import kubernetes
import json
import utils.constants as constants

logger = logging.getLogger(__name__)

class SubnetworkEvent:
    def __init__(self):
        self.cidr=None

    def handle_event(self, data):
        if 'event' in data and data['event']=='runner_on_ok':
            # if no result then wait
            if len(data['event_data']['res']['resources']) == 0:
                raise kopf.TemporaryError("No subnet object", delay=15)

            vm_k8s_object = data['event_data']['res']['resources'][0]['spec']
            self.cidr = vm_k8s_object['ipCidrRange']

async def getSubnetInfo(pdir, subnet_event, vpc):
    logger.info("building service parameters")
    logger.debug("getting %s vpc object", vpc)

    extravars = {'subnetname': vpc}
    r = ansible_runner.run(private_data_dir=pdir,
                           playbook='subnet.yaml',
                           extravars=extravars,
                           event_handler=subnet_event.handle_event)
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    if subnet_event.cidr is None:
        raise kopf.TemporaryError("No interface found", delay=15)

    logger.info('%s subnet cidr = %s', vpc, subnet_event.cidr)

async def createSite(pdir, extravars):
    logger.info("Creating site %s", extravars['sitename'])
    logger.debug(json.dumps(extravars, indent=4))
    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='createsite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)

async def deleteSite(pdir, extravars):
    logger.info("Deleting site %s", extravars['sitename'])
    logger.debug(json.dumps(extravars, indent=4))
    r = ansible_runner.run(
            private_data_dir=pdir,
            playbook='deletesite.yaml',
            extravars=extravars,
    )
    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)


def create_aend_vars(aend, bend):
    extravars = {
        'sitename': aend+'-vpn',
        'cidr': '10.10.10.0/24', 
        'project': constants.PROJECT,
        'region': constants.REGION,
        'zone': constants.ZONE,
        'mgmtsubnetname': 'mgmt-subnet',
        'vmname': aend+'-vpn',
        'interface': aend,
        'peerinterface': bend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.1',
        'peername': bend+'-vpn'
    }
    return extravars

def create_bend_vars(aend, bend):
    extravars = {
        'sitename': bend+'-vpn',
        'cidr': '10.10.11.0/24', 
        'project': constants.PROJECT,
        'region': constants.REGION,
        'zone': constants.ZONE,
        'mgmtsubnetname': 'mgmt-subnet',
        'vmname': bend+'-vpn',
        'interface': bend,
        'peerinterface': aend,
        'tunnelsubnet': '192.168.1.0/24',
        'tunneladdress': '192.168.1.2',
        'peername': aend+'-vpn'
    }
    return extravars

@kopf.on.create('connectivityservice')
async def create_connectivityservice_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create connectivityservice service handler is called with spec: {spec}")

    cwd = os.getcwd()
    pdir = cwd+"/services/sitetosite/playbooks"
    logger.info("playbook dir = %s", pdir)

    if 'type' not in spec or 'interfaces' not in spec:
        raise kopf.PermanentError("fields 'type' and 'interfaces' must be provided.")

    if spec.get("type") != "site-to-site":
        raise kopf.PermanentError("Only site-to-site supported.")

    if len(spec.get('interfaces'))!=2:
        raise kopf.PermanentError("Only two interfaces are allowed.")

    aend=spec.get('interfaces')[0]
    bend=spec.get('interfaces')[1]

    logger.info("getting %s subnet cidrs", aend)
    aendevent = SubnetworkEvent()
    await getSubnetInfo(pdir, aendevent, aend)

    bendevent = SubnetworkEvent()
    await getSubnetInfo(pdir, bendevent, bend)

    # create first site
    extravars = create_aend_vars(aend, bend)
    await createSite(pdir, extravars)

    # create site2
    extravars=create_bend_vars(aend, bend)
    await createSite(pdir, extravars)

@kopf.on.delete('connectivityservice')
async def delete_connectivityservice_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Delete connectivity service handler is called with spec: {spec}")

    cwd = os.getcwd()
    pdir = cwd+"/services/sitetosite/playbooks"
    logger.info("playbook dir = %s", pdir)

    aend=spec.get('interfaces')[0]
    bend=spec.get('interfaces')[1]

    # delete first site
    extravars=create_aend_vars(aend, bend)
    await deleteSite(pdir, extravars)

    # delete 2nd site
    extravars=create_bend_vars(aend, bend)
    await deleteSite(pdir, extravars)
