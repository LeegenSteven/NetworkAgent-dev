import kopf
import ansible_runner
import os
import logging

logger = logging.getLogger(__name__)

@kopf.on.create('connectivityservice')
async def create_wireguard_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"A service handler is called with spec: {spec}")

    if spec.get("type") != "site-to-site":
        raise kopf.PermanentError("Only site-to-site supported.")

    name = spec.get('name')
    sites = spec.get('vpcs')

    for s in sites:
        logger.info("deploy site %s", s)

        # for each site run the following
        cwd = os.getcwd()
        pdir = cwd+"/services/sitetosite/playbooks"
        r = ansible_runner.run(private_data_dir=pdir,
                            playbook='site.yaml',)
        logger.info("status = %s", r.status)
        if r.status != 'successful':
            raise kopf.TemporaryError("Ansible Error.", delay=15)
