import logging
from free5gc.controlplane.lifecycle_tasks import *
from free5gc.controlplane.k8s import *

logger = logging.getLogger(__name__)

##############################lifecycle############
# Create a new controlplane
##########################################
@kopf.on.create('google.dev', 'v1', 'controlplane')
async def control_plane(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create control plane {name} with spec: {spec}")

  # check if cluster is ready and wait if not
  cluster = await get_cluster(namespace, spec.get("cluster"))
  external_subnetwork = spec.get("external_subnetwork") 

  # check status is ready
  if cluster['status']['conditions'][0]['reason'] != "UpToDate":
      raise kopf.TemporaryError("cluster not ready, waiting...", 30)
    
  logger.debug("Cluster is running !!!!!!!!!!!!!!!!!!!!!!!!!!!!")

  # create an internal load balancer and static ip address
  # try:
  #   await get_address(namespace, name)
  # except kubernetes.client.rest.ApiException as e: 
  #   logger.info(e.status)
  #   if e.status == 404:
  #     logger.info("creating new address %s", name)
  #     await create_address(namespace, name, external_subnetwork)

  # check status of address and if not ready wait
  
  # this IP address is used by UPF and UERANSIM to access free5gc pods

  # get credentials for new cluster

  # add pods to cluster

  # track all external ip addresses for every network function in response

  return {
      "status":"Running", 
      "external_ip": "address"
  }

##########################################
# Remove a controlplane instance
##########################################
@kopf.on.delete('google.dev', 'v1', 'controlplane')
async def delete_control_plane(spec, status, namespace, name, logger, **kwargs):
  logger.debug("Remove control plane %s in ns %s", name, namespace)


  # delete pods from the cluster

  # wait for them to be deleted