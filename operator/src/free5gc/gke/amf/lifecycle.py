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
from free5gc.utils.k8s import getPodAddress
from free5gc.utils.template import *
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

##########################################
# Create a new amf
##########################################
@kopf.on.create('google.dev', 'v1', 'accessmanagementfunction')
async def accessmanagementfunction(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create amf {name} with spec: {spec}")

  yaml = YAML(typ='safe', pure=True)

  try:

    configmap_yaml=await createConfigMap("amf",namespace)
    await createDeployment("amf",namespace)
    await createService("amf",namespace)

    # load the config and attach to output
    config=yaml.load(configmap_yaml.get('data').get('amfcfg.yaml'))

    # get the controlplane ip address
    cpAddress = await getPodAddress("app=free5gc,nf=amf")
    if cpAddress is None:
      raise kopf.TemporaryError("waiting for IP address", 10)

    return {
        "status":"Running",
        "address": cpAddress,
        "port": 38412,
        "config": config
    }

  except kubernetes.client.rest.ApiException as e: 
    logger.error(e)
