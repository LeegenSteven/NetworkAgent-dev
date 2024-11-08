import logging
import kopf
from free5gc.cluster.k8s import *
import json
import kubernetes
import base64

logger = logging.getLogger(__name__)

##########################################
# List for status changes in cluster
##########################################
@kopf.on.update('gkehub.cnrm.cloud.google.com','v1beta1','gkehubfeaturememberships')
# @kopf.on.field('gkehub.cnrm.cloud.google.com','v1beta1','gkehubfeaturememberships', field='status.conditions')
async def monitorcluster(event, old, new, **_):
  logger.debug("GKE Config Sync is status change")

  logger.debug(new)

  logger.debug(json.dumps(event, indent=4))

  # if event.get('type') is not None and event.get('type') == "MODIFIED":
  #   if 'conditions' in event.get('object').get('status'):
  #     if event.get('object').get('status').get('conditions')[0].get('reason') == "Running":
  #       addSecret(event)


##########################################
# Get Cluster Spec
##########################################
async def getClusterDetails(namespace, name):
  pass

##########################################
# Add secret for git to cluster
##########################################
async def addSecret(event):
  logger.debug("adding secret to %s", event.object.metadata.name)
  client = kubernetes.dynamic.DynamicClient(get_client())

  key=None
  with open(constants.basedir+'/google-compute') as f: 
    key = f.read()
    logger.debug("ssh key = %s", key)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
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