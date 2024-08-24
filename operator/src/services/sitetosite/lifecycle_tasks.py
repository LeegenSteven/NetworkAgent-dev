import logging
import kubernetes
import kopf
import json
import os
from utils.compute import *

logger = logging.getLogger(__name__)

########################################################################
# WireguardAppliance
########################################################################
async def create_vpn_edge(vpn_name, tunnel_subnet, tunnel_ip, peer_interface, peer_vm_name, my_keys, peer_keys):
  logger.info("Create VPN Edge")

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
      "vmname": vpn_name,
      "tunnelSubnet": tunnel_subnet,
      "tunnelAddress": tunnel_ip,
      "allowedInterface": peer_interface,
      "peer": peer_vm_name,
      "keys": my_keys,
      "peerKeys": peer_keys
    }
  }

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  # logger.debug(json.dumps(crd_manifest, indent=4))

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    # logger.debug(e)
    if e.status == 409:
      logger.info("WG already exists - skipping")

########################################################################
# Create Site
########################################################################
async def create_site(aend, bend, tunnel_address, uuid, akeys, bkeys):
  logger.info("Creating VPN Site")

  # create children resources
  await create_compute(aend+'-vpn-'+uuid,
                       None,
                       aend, 
                       os.getenv("GOOGLE_PROJECT"),
                       os.getenv("GOOGLE_REGION"),
                       os.getenv("GOOGLE_ZONE"), 
                       True)

  await create_vpn_edge(aend+'-vpn-'+uuid,
                        "192.168.1.0/24",
                        tunnel_address,
                        bend, 
                        bend+'-vpn-'+uuid, 
                        akeys, 
                        bkeys)

  return vars