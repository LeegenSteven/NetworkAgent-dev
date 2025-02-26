# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
async def meshservice(body, spec, status, namespace, name, uid, logger, **kwargs):
  logger.debug(f"Creating mesh connectivity service {name} with spec: {spec}")

  kind = body.get('kind')
  interfaces = spec.get('interfaces')

  # Create the persistent config map before checking for errors
  # so that the config map and the connectivity service always
  # appear on the graph representation even in case of error.
  serviceInfo=await get_configmap(namespace, name)
  if serviceInfo is None:
    keys={}
    for interface in interfaces:
      keys[interface.get('name')]= WgKey().to_dict() 

    serviceInfo=await create_configmap(
      namespace,
      name,
      str(uuid.uuid4())[:8],
      keys
    )
  logger.debug(serviceInfo)

  # Do some sanity check
  iface_count = len(interfaces)
  if iface_count < 3:
    logger.error(f"Error creating {kind} {name}, (id: {uid}). It requires at least 3 interfaces (got {iface_count})")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")
  
  unique_inames = []
  for i in interfaces:
    iname = i['name']
    if iname not in unique_inames:
      unique_inames.append(iname)
  if len(unique_inames) != len(interfaces):
    logger.error(f"Error creating {kind} {name}, (id: {uid}). All interfaces must be distinct")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")
    
  # check the interfaces are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork",
  )
  try:
    for interface in interfaces:
      network_api.get(namespace=interface.get("namespace"), name=interface.get("name"))
  except:
    logger.error(f"Error creating {kind} {name}, (id: {uid}). Compute subnetwork {interface.get('name')} not found")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")

  # Create the initial vpn status for each instance
  vpnStatus=[]

  # create a wireguard vpn appliance for each interface
  for i in range(iface_count):
    interface = interfaces[i]
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
    await create_vpn_edge(namespace,
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
  logger.info(f"Deleting mesh service {name} in namespace {namespace}")

  # remove the configmap for this service
  await delete_configmap(namespace, name)

