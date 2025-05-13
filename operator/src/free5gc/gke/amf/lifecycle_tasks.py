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

import kubernetes
import kopf
import logging
from utils.compute import *
from jinja2 import Environment, FileSystemLoader
import os
import utils.constants as constants
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

##########################################################
# template the amf manifests
##########################################################
async def template_amf_manifest(folder, filename, ip):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        AMFADDRESS=ip
        )
    return output

##########################################
# Create a new AMF config map
##########################################
async def createAMFConfigMap(namespace, ip):
  logger.debug("Creating config map for AMF")
  yaml = YAML(typ='safe', pure=True)

  configmap_manifest = await template_amf_manifest(
     constants.basedir+"/free5gc/amf/templates/", 
     "amf-configmap.yaml",
     ip)

  configmap_manifest_yaml = yaml.load(configmap_manifest)
  kopf.adopt(configmap_manifest_yaml)

  try:
    network_api = get_resource_api("v1", configmap_manifest_yaml.get('kind'))
    network_api.create(body=configmap_manifest_yaml, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("configmap exists already - skipping")
    else:
      logger.error(e)

  return configmap_manifest_yaml
