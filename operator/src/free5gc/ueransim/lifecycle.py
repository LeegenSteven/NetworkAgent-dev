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
import kopf
from utils.compute import *
from free5gc.ueransim.lifecycle_tasks import *
from utils.k8s import getClusterDetails
from utils.resources import get_boolean_label
from free5gc.utils.k8s import get_api_client
from graph.lifecycle_tasks import update_network_node


logger = logging.getLogger(__name__)

##########################################
# Create a new ueransim
##########################################
@kopf.on.create('ueransim')
async def ueransim(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create ueransim {name} with spec: {spec}")

  # get the cell id
  cellid = spec.get('cellid')
  if cellid is None:
    raise kopf.PermanentError("no cell id provided. cant continue")

  # get the VPC name to bind UERANSIM to
  network_interface = spec.get('interface')
  logger.debug("Network %s found", network_interface)
  if network_interface is None:
    raise kopf.PermanentError("No interface found")

  controlplane_spec = spec.get('controlplane')
  if controlplane_spec is None:
    raise kopf.PermanentError("No control plane details found")

  ue_spec = spec.get('ue')
  if ue_spec is None:
    raise kopf.PermanentError("No ue details found")

  # get the controlplane instance named and grab its ip addresses and port
  controlplaneName = controlplane_spec.get('name')
  logger.debug("AMF %s found", controlplaneName)
  if controlplaneName is None or controlplaneName is None:
    raise kopf.PermanentError("controlplane name needs to be specified")  
  
  # get monitor and graph labels from the metadata / labels.
  monitor = get_boolean_label(meta, 'monitor')
  graph = get_boolean_label(meta, 'graph')

  try:
    # create UERANSIM VM on target network 
    await create_compute( namespace, 
                          name, # parent name
                          name, # vm name
                          None, # external IP
                          [network_interface], # set this to the target network name to bind to
                          os.getenv("GOOGLE_PROJECT"),
                          os.getenv("GOOGLE_REGION"),
                          os.getenv("GOOGLE_ZONE"), 
                          monitor=monitor, # set to false so this VM is not scraped by prometheus
                          graph=graph)

    controlplaneAddresses = await get_controlplane_addresses(namespace, controlplaneName)
    if controlplaneAddresses is None:
      raise kopf.TemporaryError("Waiting for control plane...", 20)

    # install UERANSIM to VM 
    await run_install(namespace, name, controlplaneAddresses['dataAddress'], controlplaneAddresses['amfPort'], controlplaneAddresses['webuiAddress'], cellid, ue_spec)

    return {
        "status":"Running", 
    }

  except kubernetes.dynamic.exceptions.ResourceNotFoundError as e:
    raise kopf.TemporaryError("Amf not running yet", 30)


##########################################
# Catch updates on status
##########################################
@kopf.on.update('ueransim', field='status')
async def ueransim_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update ueransim {name} with spec: {spec} and status: {status['ueransim']['status']}")
  kind = body.get('kind')
  await update_network_node(body, spec, namespace, name, kind, meta['uid'])