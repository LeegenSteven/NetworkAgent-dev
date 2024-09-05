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
async def delete_service_resources(name, logger, **kwargs):
  logger.debug(f"Delete pointtopoint service {name}")

  # remove the configmap for this service
  await delete_configmap(name)

############################################
# Monitor service children and update status
############################################
@kopf.on.event('google.dev', 'v1', 'WireguardAppliance')
def ptpstatus(event,meta,status, **_):
    parent_name = meta['labels']['kex-parent-name']
    name = meta['name']

    logger.debug("++++++++++++++++++++Wireguard Change Event++++++++++++++++++++++++")
    logger.debug("Parent name = %s", parent_name)
    logger.debug("Wireguard name = %s", name)

    try:
      client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
      network_api = client.resources.get(
          api_version="google.dev/v1", 
          kind="PointToPointService",
      )

      ptp = network_api.get(name=parent_name, namespace="automation")

      newptp=ptp.to_dict()

      if 'pointtopoint' in newptp['status']:
        if event.get('type')=="MODIFIED":
          if "wireguard" in status and status['wireguard']['status']=="Running":
            for edge in newptp['status']['pointtopoint']['edges']:
              if edge['name'] == name:
                edge['status']="Running"
                break

        logger.debug("====NEW STATUS ====")
        logger.debug(json.dumps(newptp['status'], indent=4))

        allRunning = True
        for edge in newptp['status']['pointtopoint']['edges']:
          if edge['status'] != "Running":
            allRunning = False

        logger.debug("-------------------------------ALL RUNNING = %s------------------------------", allRunning)

        if allRunning:
          newptp['status']['pointtopoint']['status']="Running"

        network_api.patch(body=newptp, name=parent_name, namespace="automation", content_type='application/merge-patch+json')

    except kubernetes.client.rest.ApiException as e:
      if e.status == 404:
         logger.debug("No VPN Service Found")
      else:
        logger.error(e)
