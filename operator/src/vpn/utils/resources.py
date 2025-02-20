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
import kopf
from utils.compute import *

logger = logging.getLogger(__name__)

########################################################################
# Create a WireguardAppliance instance
########################################################################
async def create_vpn_edge(namespace, parent_name, parent_namespace, parent_kind, vpn_name, source_interface, tunnel_subnet, tunnel_ip, my_keys, peers):
  logger.debug("Create VPN Edge %s in ns %s", vpn_name, namespace)
  network_api = get_resource_api("google.dev/v1", "WireguardAppliance")
  
  kind = "WireguardAppliance"
  crd_manifest = {
    "apiVersion": "google.dev/v1",
    "kind": kind,
    "metadata": {
      "name": vpn_name,
      "namespace": namespace,
      "labels": {
        "graph": "true"
      },
    },
    "spec": {
      "sourceInterface": source_interface,
      "tunnelSubnet": tunnel_subnet,
      "tunnelAddress": tunnel_ip,
      "keys": my_keys,
      "peers": peers
    }
  }

  logger.debug(json.dumps(crd_manifest, indent=4))

  # update manifest to be child of site-to-site service
  kopf.adopt(crd_manifest)
  kopf.label(crd_manifest, labels={'kex-parent-name': parent_name})
  kopf.label(crd_manifest, labels={'kex-parent-namespace': parent_namespace})
  kopf.label(crd_manifest, labels={'kex-parent-kind': parent_kind})

  try:
    result = network_api.create(body=crd_manifest, namespace=namespace)
    logger.debug("created wireguard-----------------------+====")
    logger.debug(result)
    uid = result['metadata']['uid']
    logger.info(f"Edge VPN {vpn_name} created successfully ({kind}, {uid})")
  except kubernetes.client.rest.ApiException as e: 
    logger.debug(e.status)
    if e.status == 409:
      logger.debug("WG already exists - skipping")
    else:
      logger.debug(e)
