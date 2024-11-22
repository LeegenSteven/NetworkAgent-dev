import logging
from utils.compute import *
import kopf
from utils.k8s import getClusterDetails
from free5gc.smf.lifecycle_tasks import get_k8s_manifest

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'servicemanagementfunction')
async def upf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create smf {name} with spec: {spec}")

  ########################################
  # get the ip address of the cluster
  cluster = await getClusterDetails(namespace, spec.get("clusterName"))
  publicEndPoint = cluster.get('spec').get('privateClusterConfig').get('publicEndpoint')
  logger.debug("PUBLIC ENDPOINT %s",publicEndPoint)

# Spec:
#   Private Cluster Config:
#     Master Global Access Config:
#       Enabled:         false
#     Private Endpoint:  10.0.50.4
#     Public Endpoint:   35.242.132.125

  ########################################
  # look up the UPF and get its IP address
  upfaddress=None
  upfname = spec.get("upf").get("name")
  upfnamespace = spec.get("upf").get("namespace")
  # get the ip address of the UPF
  network_api = get_resource_api("google.dev/v1", "UserPlaneFunction")
  try:
    result = network_api.get(name=upfname, namespace=upfnamespace)
    if 'upf' not in result.get('status'):
        raise kopf.TemporaryError("Waiting for upf to come up")
    upfaddress=result.get('status').get('upf').get('address')
    logger.debug("UPF ADDRESS = %s", upfaddress)
  except kubernetes.client.rest.ApiException as e: 
    logger.info(e.status)
    if e.status == 404:
        raise kopf.TemporaryError(f"No UPF {upfname} found yet. Waiting...")

  ########################################
  # render the smf manifests
  smfdeployment = await get_k8s_manifest("smf-deployment.yaml")
  logger.debug(smfdeployment)

  return {
      "status":"Running"
  }
