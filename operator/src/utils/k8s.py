import logging
import json
import kubernetes
import kopf
logger = logging.getLogger(__name__)

########################################################################
# Create configmap instance
########################################################################
async def create_configmap(name, uuid, keys):
  logger.debug("create configmap")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap")

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
