import logging
from services.sitetosite.lifecycle_tasks import *
import uuid
from utils.keys import WgKey

logger = logging.getLogger(__name__)

##########################################
# Create a new Point To Point VPN
##########################################
@kopf.on.create('pointtopointservice')
async def pointtopoint(spec, status, name, logger, **kwargs):
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
    network_api.get(namespace="automation", name=aend)
    network_api.get(namespace="automation", name=bend)
  except:
    raise kopf.PermanentError("compute sub networks not found")

  serviceInfo=await get_configmap(name)
  if serviceInfo is None:
    serviceInfo=await create_configmap(
      name,
      str(uuid.uuid4())[:8],
      WgKey().to_dict(),
      WgKey().to_dict()
    )
  logger.debug(serviceInfo)

  # Create the two wireguard virtual appliances
  asitename=aend+'-vpn-'+serviceInfo['uuid']
  bsitename=bend+'-vpn-'+serviceInfo['uuid']
  await create_vpn_edge(name,
                        asitename,
                        aend,
                        "192.168.1.0/24",
                        "192.168.1.1",
                        bend, 
                        bsitename, 
                        serviceInfo['keys']['akeys'], 
                        serviceInfo['keys']['bkeys'])
  await create_vpn_edge(name,
                        bsitename,
                        bend,
                        "192.168.1.0/24",
                        "192.168.1.2",
                        aend, 
                        asitename, 
                        serviceInfo['keys']['bkeys'],
                        serviceInfo['keys']['akeys'])

  # Create the static routes to vpn tunnels
  await create_route(aend+'-vpn-'+serviceInfo['uuid'], aend, bend)
  await create_route(bend+'-vpn-'+serviceInfo['uuid'], bend, aend)

  return {"Status": "Running"}


##########################################
# Cleanup a new PTP VPN
##########################################
@kopf.on.delete('pointtopointservice')
async def delete_service_resources(name, logger, **kwargs):
  logger.debug(f"Delete pointtopoint service {name}")

  # remove the configmap for this service
  await delete_configmap(name)

# ############################################
# # Monitor service children and update status
# ############################################
# @kopf.on.event('google.dev', 'v1', 'WireguardAppliance', labels={'kex-parent-name': None})
# def monitoring(event, meta, status, **_):
#     parent_name = meta['labels']['kex-parent-name']

#     logger.debug("event")
#     logger.debug("status")

