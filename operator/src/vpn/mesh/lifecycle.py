import logging
import uuid
from utils.keys import WgKey
import kopf
import kubernetes
from utils.k8s import *
from vpn.utils.resources import *

logger = logging.getLogger(__name__)

##########################################
# Create a new Mesh VPN
##########################################
@kopf.on.create('meshservice')
async def meshservice(spec, status, namespace, name, kind, uid, logger, **kwargs):
  logger.debug(f"Create mesh connectivity service {name} with spec: {spec}")

  if len(spec.get('interfaces'))<3:
    raise kopf.PermanentError(f"At least three interfaces must be provided ({kind}, {name}, {uid})")

  # check the interfaces are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork",
  )
  try:
    for interface in spec.get('interfaces'):
      network_api.get(namespace=interface.get("namespace"), name=interface.get("name"))
  except:
    raise kopf.PermanentError(f"Compute subnetworks missing {interface.get['name']} ({kind}, {name}, {uid})")

  # create persistent config for the service
  serviceInfo=await get_configmap(namespace, name)
  if serviceInfo is None:
    keys={}
    for interface in spec.get('interfaces'):
      keys[interface.get('name')]= WgKey().to_dict() 

    serviceInfo=await create_configmap(
      namespace,
      name,
      str(uuid.uuid4())[:8],
      keys
    )
  logger.debug(serviceInfo)

  # Create the initial vpn status for each instance
  vpnStatus=[]

  # create a wireguard vpn appliance for each interface
  for i in range(len(spec['interfaces'])):
    interface = spec['interfaces'][i]
    allowedInterfaces = spec['interfaces'][:i] + spec['interfaces'][i + 1:]

    logger.debug("building mesh for interface %s", interface.get("name"))
    logger.debug("allowed interfaces = %s", allowedInterfaces)

    instanceName = interface.get("name")+'-vpn-'+serviceInfo['uuid']
    vpnStatus.append({"name": instanceName, "status": "Pending"})

    peers=[]
    for allowedInterface in allowedInterfaces:
      peers.append({
                   "peerName": allowedInterface.get("name")+'-vpn-'+serviceInfo['uuid'], 
                   "allowedInterface": allowedInterface, 
                   "keys": serviceInfo['keys'][allowedInterface.get('name')]
                  })

    logger.debug(json.dumps(peers, indent=4))

    # deploy the vpn virtual machine in the same namespace as the network object
    await create_vpn_edge( namespace,
                           name,
                           namespace,
                          "meshservice",
                          instanceName,
                          interface,
                          "192.168.1.0/24",
                          "192.168.1."+str(i+1),
                          serviceInfo['keys'][interface.get('name')], 
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
async def delete_service_resources(namespace, name, logger, **kwargs):
  logger.debug(f"Delete mesh service {name} in ns {namespace}")

  # remove the configmap for this service
  await delete_configmap(namespace, name)

