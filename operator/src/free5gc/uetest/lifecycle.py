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
import kopf
from free5gc.uetest.lifecycle_tasks import *
from utils.compute import *
from free5gc.utils.k8s import getDNNAddress

logger = logging.getLogger(__name__)

##########################################
# Create a new UE Test
##########################################
@kopf.on.create('google.dev', 'v1', 'uetest')
async def uetest(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create ue test {name} with spec: {spec}")

  # get the name of the UERanSim VM
  vmname = spec.get('ueransim')
  if vmname is None:
    raise kopf.PermanentError("No ueransim found")

  dnn = spec.get('datanetwork')
  if dnn is None:
    raise kopf.PermanentError("No data network found")

  dnn_address = await getDNNAddress(namespace, dnn['name'])
  if dnn_address is None:
     raise kopf.PermanentError("No DNN address")

  dnn_url = f"http://{dnn_address}"

  await run_test(namespace, vmname['name'],dnn_url)

  return {
      "status":"Running",
  }

##########################################
# Delete UE Test
##########################################
@kopf.on.delete('google.dev', 'v1', 'uetest')
async def uetest(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Delete ue test {name} with spec: {spec}")

  # get the name of the UERanSim VM
  vmname = spec.get('ueransim')
  if vmname is None:
    raise kopf.PermanentError("No ueransim found")

  await stop_test(namespace, vmname['name'])

  return {
      "status":"stopped",
  }
