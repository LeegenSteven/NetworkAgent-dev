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
import base64
import logging

logger = logging.getLogger(__name__)


########################################################################
# Create namespace instance in other cluster
########################################################################
async def create_namespace(client, namespace):
  logger.debug("create namespace %s", namespace)

  client = kubernetes.dynamic.DynamicClient(client)
  api = client.resources.get(api_version="v1", kind="Namespace")

  manifest = {
      "kind": "Namespace",
      "apiVersion": "v1",
      "metadata": {
          "name": namespace,
      }
  }
  logger.debug(manifest)
  try:
    api.create(body=manifest)
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    logger.debug(e)
    if e.status == 409:
      logger.debug("namespace already exists - skipping")

##########################################
# Add secret for git to cluster
##########################################
async def addSecret(client):
  logger.debug("adding secret",)

  key=None
  with open('/operator/google-compute') as f:
    key = f.read()
    logger.debug("ssh key = %s", key)

  client = kubernetes.dynamic.DynamicClient(client)
  network_api = client.resources.get(
      api_version="v1", 
      kind="Secret",
  )

  crd_manifest= {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {
      "name": "git-creds",
      "namespace": "config-management-system"
    },
    "data": {
      "ssh": base64.b64encode(key.encode('utf-8')).decode('utf-8')
    }
  }

  try:
    result = network_api.create(body=crd_manifest, namespace="config-management-system")
    return result
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("Already exists - skipping")
    else:
      logger.debug(e)