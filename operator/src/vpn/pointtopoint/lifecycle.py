import logging
from vpn.utils.resources import *
import uuid
from utils.keys import WgKey
from utils.k8s import *

logger = logging.getLogger(__name__)

##########################################
# Create a new Point To Point VPN
##########################################
@kopf.on.create('pointtopointservice')
async def pointtopointservice(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create pointtopoint service {name} with spec: {spec}")

  if len(spec.get('interfaces'))!=2:
    raise kopf.PermanentError("Two interfaces must be provided.")

  # get the a and b end variables
  aend=spec.get('interfaces')[0]
  bend=spec.get('interfaces')[1]

  # check that aend and bend are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork",
  )
  try:
    network_api.get(namespace=namespace, name=aend)
    network_api.get(namespace=namespace, name=bend)
  except:
    raise kopf.PermanentError("compute sub networks not found")

  # if persistent config for this service exists, grab it
  # if this is first time this is called then create it
  serviceInfo=await get_configmap(namespace, name)
  if serviceInfo is None:
    keys={
      "akeys": WgKey().to_dict(),
      "bkeys": WgKey().to_dict()
    }
    serviceInfo=await create_configmap(
      namespace,
      name,
      str(uuid.uuid4())[:8],
      keys
    )
  logger.debug(serviceInfo)

  # create instance and peer information
  asitename=aend+'-vpn-'+serviceInfo['uuid']
  bsitename=bend+'-vpn-'+serviceInfo['uuid']
  asitepeers=[{"peerName": bsitename, "allowedInterface": bend, "keys": serviceInfo['keys']['bkeys']}]
  bsitepeers=[{"peerName": asitename, "allowedInterface": aend, "keys": serviceInfo['keys']['akeys']}]

  await create_vpn_edge(namespace,
                        name,
                        "pointtopointservice",
                        asitename,
                        aend,
                        "192.168.1.0/24",
                        "192.168.1.1",
                        serviceInfo['keys']['akeys'], 
                        asitepeers)

  await create_vpn_edge(namespace,
                        name,
                        "pointtopointservice",
                        bsitename,
                        bend,
                        "192.168.1.0/24",
                        "192.168.1.2",
                        serviceInfo['keys']['bkeys'], 
                        bsitepeers)

  return {
    "status": "Pending",
    "edges": [
       { "name" : asitename, "status" : "Pending" },
       { "name" : bsitename, "status" : "Pending" }
    ]
  }


##########################################
# Cleanup a new PTP VPN
##########################################
@kopf.on.delete('pointtopointservice')
async def delete_service_resources(namespace, name, logger, **kwargs):
  logger.debug(f"Delete pointtopoint service {name} in ns {namespace}")

  # remove the configmap for this service
  await delete_configmap(namespace, name)

