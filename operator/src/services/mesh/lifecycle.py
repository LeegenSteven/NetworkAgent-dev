import logging
import uuid
from utils.keys import WgKey
import kopf
import kubernetes
from utils.k8s import *
from services.utils.resources import *

logger = logging.getLogger(__name__)

##########################################
# Create a new Mesh VPN
##########################################
@kopf.on.create('meshservice')
async def meshservice(spec, status, name, logger, **kwargs):
  logger.debug(f"Create mesh connectivity service {name} with spec: {spec}")

  if len(spec.get('interfaces'))<3:
    raise kopf.PermanentError("At least three interfaces must be provided.")

  # check the interfaces are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork",
  )
  try:
    for interface in spec.get('interfaces'):
      network_api.get(namespace="automation", name=interface)
  except:
    raise kopf.PermanentError("compute sub networks not found")

  # create persistent config for the service
  serviceInfo=await get_configmap(name)
  if serviceInfo is None:

    keys={}
    for interface in spec.get('interfaces'):
      keys[interface]= WgKey().to_dict() 

    serviceInfo=await create_configmap(
      name,
      str(uuid.uuid4())[:8],
      keys
    )
  logger.debug(serviceInfo)

  # Create the initial vpn status for each instance
  vpnStatus=[]

  # create a wireguard vpn appliance for each interface
  for i in range(len(spec['interfaces'])):
    interfaceName = spec['interfaces'][i]
    allowedInterfaces = spec['interfaces'][:i] + spec['interfaces'][i + 1:]

    logger.debug("building mesh for interface %s", interfaceName)
    logger.debug("allowed interfaces = %s", allowedInterfaces)

    instanceName = interfaceName+'-vpn-'+serviceInfo['uuid']
    vpnStatus.append({"name": instanceName, "status": "Pending"})

    peers=[]
    for allowedInterface in allowedInterfaces:
      peers.append({
                   "peerName": allowedInterface+'-vpn-'+serviceInfo['uuid'], 
                   "allowedInterface": allowedInterface, 
                   "keys": serviceInfo['keys'][allowedInterface]
                  })

    logger.debug(json.dumps(peers, indent=4))

    await create_vpn_edge(name,
                          "meshservice",
                          instanceName,
                          interfaceName,
                          "192.168.1.0/24",
                          "192.168.1."+str(i+1),
                          serviceInfo['keys'][interfaceName], 
                          peers)


  returnStatus={
    "status": "Pending",
    "edges": vpnStatus
  }

  return returnStatus

##########################################
# Cleanup a new Mesh VPN
##########################################
@kopf.on.delete('meshservice')
async def delete_service_resources(name, logger, **kwargs):
  logger.debug(f"Delete mesh service {name}")

  # remove the configmap for this service
  await delete_configmap(name)

