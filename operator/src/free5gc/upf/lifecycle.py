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
from utils.compute import *
from utils.resources import get_boolean_label
import kopf
from free5gc.upf.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'userplanefunction')
async def userplanefunction(meta, spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create upf {name} with spec: {spec}")

  # get the VPCs to bind UPF to
  ingress = spec.get('ingress')
  egress = spec.get('egress')

  # get monitor and graph labels from the metadata / labels.
  monitor = get_boolean_label(meta, 'monitor')
  graph = get_boolean_label(meta, 'graph')
  
  # create UPF VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        [ ingress, egress], # set this to the target network names to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        release="ubuntu-minimal-pro-2004-lts",
                        monitor=monitor, # set to false so this VM is not scraped by prometheus
                        graph=graph)

  # install UPF to VM 
  await run_install(namespace, name)
  mgmtIP=await get_ip(namespace, name)
  ingressIP=await get_ip(namespace, name, ingress.get('name'))
  egressIP=await get_ip(namespace, name, egress.get('name'))

  return {
      "status":"Running",
      "mgmtAddress": mgmtIP,
      "ingressAddress": ingressIP,
      "egressAddress": egressIP
  }
