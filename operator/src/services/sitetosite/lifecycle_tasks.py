import logging
import kubernetes
import kopf
from utils.compute import *
import json

logger = logging.getLogger(__name__)

########################################################################
# Create configmap instance
########################################################################
async def create_configmap(name, uuid, akeys, bkeys):
  logger.debug("create configmap")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap")

  keys={
    "akeys": akeys,
    "bkeys": bkeys
  }

  configmap_manifest = {
      "kind": "ConfigMap",
      "apiVersion": "v1",
      "metadata": {
          "name": name,
      },
      "data": {
          "uuid": uuid,
          "keys": json.dumps(keys)
      },
  }
  logger.debug(configmap_manifest)

  kopf.adopt(configmap_manifest)
  kopf.label(configmap_manifest, labels={'kex-parent-name': name})

  try:
    api.create(body=configmap_manifest, namespace="automation")

    returnObject={
      "uuid": uuid,
      "keys": keys
    }
    return returnObject

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 409:
      logger.debug("configmap already exists - skipping")

########################################################################
# Get configmap instance
########################################################################
async def get_configmap(name):
  logger.debug("getting config name %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap") 

  try:

    result=api.get(name=name,namespace="automation")
    logger.debug(result)
    keystring=result.get('data').get('keys')
    if keystring is not None:
      return {
        "uuid": result.get('data').get('uuid'), 
        "keys": json.loads(keystring)
      }
    return None

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
      logger.debug("no configmap named %s", name)
      return None
    else:
      logger.debug(e)

########################################################################
# delete configmap instance
########################################################################
async def delete_configmap(name):
  logger.debug("getting config name %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap") 

  try:
    api.delete(name=name,namespace="automation")
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 404:
      logger.error("no configmap named %s", name)

########################################################################
# Create a WireguardAppliance instance
########################################################################
async def create_vpn_edge(parent_name, vpn_name, source_interface,tunnel_subnet, tunnel_ip, peer_interface, peer_vm_name, my_keys, peer_keys):
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
      "sourceInterface": source_interface,
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
  kopf.label(crd_manifest, labels={'kex-parent-name': parent_name})

  try:
    result = network_api.create(crd_manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("WG already exists - skipping")
    else:
      logger.debug(e)
