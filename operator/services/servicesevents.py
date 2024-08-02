import kopf
import ansible_runner
import os
import logging

logger = logging.getLogger(__name__)

@kopf.on.create('connectivityservice')
async def create_connectivityservice_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create connectivityservice service handler is called with spec: {spec}")

    cwd = os.getcwd()
    pdir = cwd+"/services/sitetosite/playbooks"

    if spec.get("type") != "site-to-site":
        raise kopf.PermanentError("Only site-to-site supported.")

    name = spec.get('name')
    vpcs = spec.get('vpcs')

    for site in vpcs:
        logger.info("deploy site %s", site)

        # for each site run the following
        extravars = {
            'sitename': '',  # name of the site
            'mgmtsubnet': '', # name of the mgmt subnet
            'cidr': '',      # CIDR of the subnet at the new network for the vrouter 
            'region': '',    # region
            'zone': '',      # zone
            'vmname': '',    # name of the compute instance
            'interface': '', # name of the interface to connect to site
        }
        r = ansible_runner.run(
                private_data_dir=pdir,
                playbook='site.yaml',
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

    name = spec.get('name')
    sites = spec.get('vpcs')

    for s in sites:
        logger.info("deploy site %s", s)

        # for each site run the following
        extravars = {
            'sitename': '',  # name of the site
            'cidr': '',      # CIDR of the subnet at the new network for the vrouter 
            'region': '',    # region
            'zone': '',      # zone
            'vmname': '',    # name of the compute instance
            'interface': '', # name of the interface to connect to site
        }
        r = ansible_runner.run(
                private_data_dir=pdir,
                playbook='site.yaml',
                extravars=extravars,
        )
        logger.info("status = %s", r.status)
        if r.status != 'successful':
            raise kopf.TemporaryError("Ansible Error.", delay=15)
