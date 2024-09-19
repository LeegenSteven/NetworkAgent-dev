import logging
import kubernetes
import kopf
from utils.compute import *

logger = logging.getLogger(__name__)

########################################################################
# Create a WireguardAppliance instance
########################################################################
async def create_vpn_edge(parent_name, parent_kind, vpn_name, source_interface,tunnel_subnet, tunnel_ip, my_keys, peers):
  logger.debug("Create VPN Edge %s", vpn_name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
      api_version="google.dev/v1", 
      kind="WireguardAppliance",
  )

  crd_manifest = {
    "apiVersion": "google.dev/v1",
    "kind": "WireguardAppliance",
    "metadata": {
      "name": vpn_name,
      "namespace": "automation"
    },
    "spec": {
      "sourceInterface": source_interface,
      "tunnelSubnet": tunnel_subnet,
      "tunnelAddress": tunnel_ip,
      "keys": my_keys,
      "peers": peers
    }
  }

  logger.debug(json.dumps(crd_manifest, indent=4))

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  kopf.label(crd_manifest, labels={'kex-parent-name': parent_name})
  kopf.label(crd_manifest, labels={'kex-parent-kind': parent_kind})

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("WG already exists - skipping")
    else:
      logger.debug(e)
