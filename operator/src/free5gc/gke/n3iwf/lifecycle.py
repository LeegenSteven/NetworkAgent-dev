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
from free5gc.utils.template import template_manifest
from ruamel.yaml import YAML
import kubernetes

logger = logging.getLogger(__name__)

##########################################
# Create a new n3iwf
##########################################
@kopf.on.create('google.dev', 'v1', 'interworkingfunction')
async def interworkingfunction(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create n3iwf {name} with spec: {spec}")

  # deploy the manifest to the current cluster
  yaml = YAML(typ='safe', pure=True)

  try:

    configmap_manifest = await template_manifest(constants.basedir+"/free5gc/n3iwf/templates/", "n3iwf-configmap.yaml")
    configmap_manifest_yaml = yaml.load(configmap_manifest)
    kopf.adopt(configmap_manifest_yaml)
    network_api = get_resource_api("v1", configmap_manifest_yaml.get('kind'))
    network_api.create(body=configmap_manifest_yaml, namespace=namespace)

    deployment_manifest = await template_manifest(constants.basedir+"/free5gc/n3iwf/templates/", "n3iwf-deployment.yaml")
    deployment_manifest_yaml = yaml.load(deployment_manifest)
    kopf.adopt(deployment_manifest_yaml)
    network_api = get_resource_api("v1", deployment_manifest_yaml.get('kind'))
    network_api.create(body=deployment_manifest_yaml, namespace=namespace)

    service_manifest = await template_manifest(constants.basedir+"/free5gc/n3iwf/templates/", "n3iwf-service.yaml")
    service_manifest_yaml = yaml.load(service_manifest)
    kopf.adopt(service_manifest_yaml)
    network_api = get_resource_api("v1", service_manifest_yaml.get('kind'))
    network_api.create(body=service_manifest_yaml, namespace=namespace)


    # load the config and attach to output
    config=yaml.load(configmap_manifest_yaml.get('data').get('n3iwfcfg.yaml'))

    return {
        "status":"Running",
        "config": config
    }

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 422:
      raise kopf.PermanentError("Unprocessable entity.")
    elif e.status == 409:
      logger.debug("already exists - skipping")
    else:
      logger.debug(e)
