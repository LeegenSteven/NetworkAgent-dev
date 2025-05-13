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
from free5gc.smf.lifecycle_tasks import getUPFAddress, createSMFConfigMap, createGKEIPRoute
from free5gc.utils.k8s import getPodAddress
from free5gc.utils.template import *
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunctionget_resource_api
##########################################
@kopf.on.create('google.dev', 'v1', 'sessionmanagementfunction')
async def sessionmanagementfunction(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create smf {name} with spec: {spec}")

  # get the ip address of the spec named UPF
  upfname = spec.get("upf").get("name")
  upfnamespace = spec.get("upf").get("namespace")
  upfaddress=await getUPFAddress(upfname, upfnamespace)

  # deploy the manifest to the current cluster
  yaml = YAML(typ='safe', pure=True)

  try:
    configmap_yaml=await createSMFConfigMap(namespace, upfname, upfaddress,"10.3.0.100")
    await createDeployment("smf",namespace)
    await createService("smf",namespace)

    # load the config and attach to output
    config=yaml.load(configmap_yaml.get('data').get('smfcfg.yaml'))

    # cpAddress = await getPodAddress("app=free5gc,nf=smf")
    # if cpAddress is None:
    #   raise kopf.TemporaryError("waiting for ip address", 10)

    return {
        "status":"Running",
        "address": "10.3.0.100",
        "port": 8805,
        "config": config
    }

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    else:
      logger.debug(e)

