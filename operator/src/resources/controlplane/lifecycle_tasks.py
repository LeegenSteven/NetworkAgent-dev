import logging
import kubernetes
from utils.compute import *

logger = logging.getLogger(__name__)

####################################################
# Get a cluster object
####################################################
async def get_cluster(namespace, name):
  logger.debug("Get cluster %s in ns %s", name, namespace)

  network_api = get_resource_api("container.cnrm.cloud.google.com/v1beta1", "ContainerCluster")
  try:
    result = network_api.get(namespace=namespace, name=name)
    return result

  except kubernetes.client.rest.ApiException as e:
    if e.status == 404:
      raise kopf.TemporaryError(f"No cluster {name} found in ns {namespace}, waiting...", 30)
    else:
      logger.debug(e)
      return None


####################################################
# Get an internal address object
####################################################
async def get_address(namespace, name):
  logger.debug("getting internal ip address %s", name)
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeAddress")
  result=compute_api.get(name=name,namespace=namespace)
  logger.debug(result)
  return result

####################################################
# Create a new internal address resource
####################################################
async def create_address(namespace, name, subnetwork):
  logger.debug("getting internal ip address %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  compute_api = get_resource_api("compute.cnrm.cloud.google.com/v1beta1", "ComputeAddress")

  address_manifest = {
      "kind": "ComputeAddress",
      "metadata": {
          "name": name,
          "annotations": {
            "configmanagement.gke.io/managed": "disabled"
          }
      },
      "spec": {
        "addressType": "INTERNAL",
        "description": "internal address",
        "location": os.getenv("GOOGLE_REGION"),
        "subnetworkRef": {
          "name": subnetwork['name'], 
          "namespace": subnetwork['namespace']
        }
      },
  }
  logger.debug(address_manifest)

  kopf.adopt(address_manifest)
  kopf.label(address_manifest, labels={'kex-parent-name': name})

  try:
    compute_api.create(body=address_manifest, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("address already exists - skipping")
    else:
      logger.debug(e)


####################################################
# Template a free5gc descriptor
####################################################
async def get_free5gc_manifest(name):
  logger.debug("getting free5gc manifest for %s", name)

  