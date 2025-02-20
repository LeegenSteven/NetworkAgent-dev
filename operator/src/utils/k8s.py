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
import json
import kubernetes
import kopf
logger = logging.getLogger(__name__)
from utils.compute import get_resource_api

########################################################################
# Create configmap instance
########################################################################
async def create_configmap(namespace, name, uuid, keys):
  logger.debug("create configmap")

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap")

  configmap_manifest = {
      "kind": "ConfigMap",
      "apiVersion": "v1",
      "metadata": {
          "name": name,
          "annotations": {
            "configmanagement.gke.io/managed": "disabled"
          }
      },
      "data": {
          "uuid": uuid,
          "keys": json.dumps(keys)
      },
  }
  logger.debug(configmap_manifest)

  kopf.adopt(configmap_manifest)
  kopf.label(configmap_manifest, labels={'kex-parent-name': name})

  try:
    api.create(body=configmap_manifest, namespace=namespace)

    returnObject={
      "uuid": uuid,
      "keys": keys
    }
    return returnObject

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 409:
      logger.debug("configmap already exists - skipping")

########################################################################
# Get configmap instance
########################################################################
async def get_configmap(namespace, name):
  logger.debug("getting config name %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap") 

  try:

    result=api.get(name=name,namespace=namespace)
    logger.debug(result)
    keystring=result.get('data').get('keys')
    if keystring is not None:
      return {
        "uuid": result.get('data').get('uuid'), 
        "keys": json.loads(keystring)
      }
    return None

  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 404:
      logger.debug("no configmap named %s", name)
      return None
    else:
      logger.debug(e)

########################################################################
# delete configmap instance
########################################################################
async def delete_configmap(namespace, name):
  logger.debug("getting config name %s", name)

  client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
  api = client.resources.get(api_version="v1", kind="ConfigMap") 

  try:
    api.delete(name=name,namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 404:
      logger.error("no configmap named %s", name)


##########################################
# Get Cluster Spec
##########################################
async def getClusterDetails(namespace, name):
  logger.debug("Get container cluster %s in namespace %s", name, namespace)
  network_api = get_resource_api("container.cnrm.cloud.google.com/v1beta1", "ContainerCluster")
  try:
    result = network_api.get(namespace=namespace, name=name)
    return result
  except kubernetes.client.rest.ApiException as e:
    if e.status == 404:
      logger.debug("%s in namespace %s Not found", name, namespace)
    else:
      logger.debug(e)
  return None

##########################################
# Get Cluster Feature Spec
##########################################
async def getClusterFeatureDetails(namespace, name):
  logger.debug("Get container cluster feature %s in namespace %s", name, namespace)
  network_api = get_resource_api("gkehub.cnrm.cloud.google.com/v1beta1", "GKEHubFeature")
  try:
    result = network_api.get(namespace=namespace, name=name)
    return result
  except kubernetes.client.rest.ApiException as e:
    if e.status == 404:
      logger.debug("%s in namespace %s Not found", name, namespace)
    else:
      logger.debug(e)
  return None
