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
import kubernetes
from agent_library.credentials.k8s import get_client

logger = logging.getLogger(__name__)

#######################################################
# Get the mgmt ip address for a computeinstance
#######################################################
async def get_ip_address(computename):
  logger.info(f"getting mgmt ip address for {computename}")

  client = kubernetes.dynamic.DynamicClient(get_client())
  addressname = computename+"-mgmt-address"

  try:
    network_api = client.resources.get(
        api_version="compute.cnrm.cloud.google.com/v1beta1", 
        kind="ComputeAddress",
    )
    result=network_api.get(name=addressname, namespace="network")
    logger.info(result)
    obs_state = result.get('status').get('observedState')
    if obs_state is None:
        raise Exception("no address state")
    return obs_state.get('address')

  except kubernetes.client.rest.ApiException as e:
      if e.status == 404:
          return None
      else:
          logger.info(e)
