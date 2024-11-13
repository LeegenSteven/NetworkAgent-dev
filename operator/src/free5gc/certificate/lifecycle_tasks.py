import kubernetes
import base64
from free5gc.certificate.k8s import *



########################################################################
# Create namespace instance in other cluster
########################################################################
async def create_namespace(client, namespace):
  logger.debug("create namespace %s", namespace)

  client = kubernetes.dynamic.DynamicClient(client)
  api = client.resources.get(api_version="v1", kind="Namespace")

  manifest = {
      "kind": "Namespace",
      "apiVersion": "v1",
      "metadata": {
          "name": namespace,
      }
  }
  logger.debug(manifest)
  try:
    api.create(body=manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 409:
      logger.debug("namespace already exists - skipping")

##########################################
# Add secret for git to cluster
##########################################
async def addSecret(client):
  logger.debug("adding secret",)

  key=None
  with open('/operator/google-compute') as f:
    key = f.read()
    logger.debug("ssh key = %s", key)

  client = kubernetes.dynamic.DynamicClient(client)
  network_api = client.resources.get(
      api_version="v1", 
      kind="Secret",
  )

  crd_manifest= {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {
      "name": "git-creds",
      "namespace": "config-management-system"
    },
    "data": {
      "ssh": base64.b64encode(key.encode('utf-8')).decode('utf-8')
    }
  }

  try:
    result = network_api.create(body=crd_manifest, namespace="config-management-system")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)