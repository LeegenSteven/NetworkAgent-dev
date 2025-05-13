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
from free5gc.utils.template import *
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

##########################################
# Create a new udm
##########################################
@kopf.on.create('google.dev', 'v1', 'unifieddatamanagementfunction')
async def unifieddatamanagementfunction(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create udm {name} with spec: {spec}")

  # deploy the manifest to the current cluster
  yaml = YAML(typ='safe', pure=True)

  try:

    configmap_yaml=await createConfigMap("udm",namespace)
    await createDeployment("udm",namespace)
    await createService("udm",namespace)

    # load the config and attach to output
    config=yaml.load(configmap_yaml.get('data').get('udmcfg.yaml'))

    return {
        "status":"Running",
        "config": config
    }
  
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    else:
      logger.debug(e)
