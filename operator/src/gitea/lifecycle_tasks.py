import kopf
import logging
from utils.compute import *
import utils.constants as constants
import ansible_runner
from vpn.wireguard.lifecycle_tasks import *;

logger = logging.getLogger(__name__)

########################################################
# Install and configure gitea software
########################################################
async def run_gitea_install(namespace, external_ip_address):
    logger.debug("installing prometheus monitor")

    ip_address = await get_ip(namespace, "gitea")
    if ip_address is None:
        raise kopf.TemporaryError("waiting for gitea IP address")

    # run ansible playbook to install prometheus on the VM
    extravars = {
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir,
        'external_ip_address': external_ip_address
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
    def event_handler(data):
        logger.debug(data)
    r = ansible_runner.run(private_data_dir=constants.basedir+"/gitea/playbooks", 
                           inventory={'all': hosts},
                           playbook='install.yaml',
                           event_handler=event_handler,
                           extravars=extravars)

    logger.debug("status = %s", r.status)
    if r.status != 'successful':
        logger.debug(r.status)
        raise kopf.TemporaryError("Ansible Error!!!",15)


########################################################
# Create root sync in network automation cluster
########################################################
async def create_root_sync(ip_address):
  logger.debug(f"create root sync to repo {ip_address}")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="configsync.gke.io/v1beta1", 
      kind="RootSync",
  )

  crd_manifest= {
    "apiVersion": "configsync.gke.io/v1beta1",
    "kind": "RootSync",
    "metadata": {
      "name": "networkagent-root",
      "namespace": "config-management-system"
    },
    "spec": {
      "sourceType": "git",
      "sourceFormat": "unstructured",
      "git": {
        "repo": f"https://networkagent:password123@{ip_address}:3000/networkagent/root-repo",
        "auth": "none",
        "revision": "HEAD",
        "branch": "master",
        "gcpServiceAccountEmail": f"networkagent@{os.getenv("GOOGLE_PROJECT")}.iam.gserviceaccount.com",
        "noSSLVerify": True
      }
    }
  }

  try:
    result = network_api.create(crd_manifest)
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)
