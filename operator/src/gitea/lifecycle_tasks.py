import kopf
import logging
from utils.compute import *
import utils.constants as constants
import ansible_runner
from resources.wireguard.lifecycle_tasks import *;

logger = logging.getLogger(__name__)

async def run_gitea_install():
    logger.debug("installing prometheus monitor")

    ip_address = await get_ip("gitea")
    if ip_address is None:
        raise kopf.TemporaryError("waiting for gitea IP address")

    # run ansible playbook to install prometheus on the VM
    extravars = {
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir
    }
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': ip_address,
                'ansible_user': os.getenv("GOOGLE_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.debug(hosts)
    logger.debug(extravars)
    r = ansible_runner.run(private_data_dir=constants.basedir+"/gitea/playbooks", 
                           inventory={'all': hosts},
                           playbook='install.yaml',
                           extravars=extravars)

    logger.debug("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    

    # create the configsync configuration
    # apply-spec.yaml

    # applySpecVersion: 1
    # spec:
    # configSync:
    #     # Set to true to install and enable Config Sync
    #     enabled: true
    #     # If you don't have a source of truth yet, omit the
    #     # following fields. You can configure them later.
    #     sourceType: git
    #     syncRepo: http://{ip_address}/
    #     syncBranch: production
    #     secretType: ssh
    #     gcpServiceAccountEmail: "networkagent@{{GOOGLE_PROJECT}}.iam.gserviceaccount.com"
    #     metricsGcpServiceAccountEmail: "networkagent@{{GOOGLE_PROJECT}}.iam.gserviceaccount.com"
    #     policyDir: /production
    #     preventDrift: true
