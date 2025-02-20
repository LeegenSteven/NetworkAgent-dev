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
from vpn.utils.resources import *
import uuid
from utils.keys import WgKey
from utils.k8s import *

logger = logging.getLogger(__name__)

##########################################
# Create a new Point To Point VPN
##########################################
@kopf.on.create('pointtopointservice')
async def pointtopointservice(body, spec, status, namespace, name, uid, logger, **kwargs):
  logger.debug(f"Create pointtopoint service {name} with spec: {spec}")

  kind = body.get('kind')

  # create persistent config for the service. Do this before
  # checking for errors so that if there is any error 
  # subsequently in creating the mesh service the configmap
  # appears a link to the mesh service on the graph representation
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

  # Do some sanity check
  iface_count = len(spec.get('interfaces'))
  if iface_count != 2:
    logger.error(f"Error creating {kind} {name}, (id: {uid}). It requires exactly 2 interfaces (got {iface_count})")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")

  # get the a and b end variables
  aend=spec.get('interfaces')[0]
  bend=spec.get('interfaces')[1]

  if aend.get('name') == bend.get('name'):
    logger.error(f"Error creating {kind} {name}, (id: {uid}). The 2 interfaces must be distinct")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")

  # check that aend and bend are valid computesubnetworks
  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  network_api = client.resources.get(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork",
  )
  try:
    for interface in [aend, bend]:
      network_api.get(namespace=interface.get("namespace"), name=interface.get("name"))
  except:
    logger.error(f"Error creating {kind} {name}, (id: {uid}). Compute subnetwork {interface.get('name')} not found")
    raise kopf.PermanentError(f"Failed creating {kind} {name}")

  # create instance and peer information
  asitename=aend.get("name")+'-vpn-'+serviceInfo['uuid']
  bsitename=bend.get("name")+'-vpn-'+serviceInfo['uuid']
  asitepeers=[{"peerName": bsitename, "allowedInterface": bend, "keys": serviceInfo['keys']['bkeys']}]
  bsitepeers=[{"peerName": asitename, "allowedInterface": aend, "keys": serviceInfo['keys']['akeys']}]

  await create_vpn_edge(namespace,
                        name,
                        namespace,
                        "pointtopointservice",
                        asitename,
                        aend,
                        "192.168.1.0/24",
                        "192.168.1.1",
                        serviceInfo['keys']['akeys'], 
                        asitepeers)

  await create_vpn_edge(namespace,
                        name,
                        namespace,
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

