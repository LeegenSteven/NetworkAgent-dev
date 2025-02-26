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
from utils.k8s import *
from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

############################################
# Monitor service children and update status
############################################
@kopf.on.event('google.dev', 'v1', 'WireguardAppliance')
async def servicestatus(event, meta, namespace, status, name, uid, **_):
    parent_name = meta['labels']['kex-parent-name']
    parent_namespace = meta['labels']['kex-parent-namespace']
    parent_kind = meta['labels']['kex-parent-kind']

    logger.info(f"Monitoring status of VPN {name}... ")

    logger.debug("++++++++++++++++++++Wireguard Change Event++++++++++++++++++++++++")
    logger.debug("Parent name = %s", parent_name)
    logger.debug("Parent namespace = %s", parent_namespace)
    logger.debug("Parent kind = %s", parent_kind)
    logger.debug("Wireguard name = %s", name)

    try:
      updateStatus(namespace, parent_kind, parent_name, parent_namespace, status, event, name, uid)
    except kubernetes.client.rest.ApiException as e:
      if e.status == 404:
        logger.debug("No VPN Service Found")
      elif e.status == 409:
        logger.debug("Conflict - manifest is out of date - reload and  try again!!!!!!!!!!!!")
        updateStatus(namespace, parent_kind, parent_name, parent_namespace, status, event, name, uid)
      else:
        logger.error(e)

def updateStatus(namespace, parent_kind, parent_name, parent_namespace, status, event, name, uid):
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    kind = None
    if parent_kind=="pointtopointservice":
      kind = "PointToPointService"
    elif parent_kind=="meshservice": 
      kind = "MeshService"
    else:
      raise kopf.PermanentError("unknown service kind")
    network_api = client.resources.get(
        api_version="google.dev/v1", 
        kind=kind,
    )

    service = network_api.get(name=parent_name, namespace=parent_namespace)
    newservice=service.to_dict()

    if parent_kind in newservice['status']:
      if event.get('type')=="MODIFIED":
        if "wireguard" in status and status['wireguard']['status']=="Running":
          for edge in newservice['status'][parent_kind]['edges']:
            if edge['name'] == name:
              previous_status = edge['status']
              edge['status'] = "Running"
              if previous_status != edge['status']: 
                logger.info(f"VPN edge {name} of service {parent_name} status updated to {edge['status']}")
              break

      logger.debug("====NEW STATUS ====")
      logger.debug(json.dumps(newservice['status'], indent=4))

      allRunning = True
      for edge in newservice['status'][parent_kind]['edges']:
        if edge['status'] != "Running":
          allRunning = False

      logger.debug("-------------------------------ALL RUNNING = %s------------------------------", allRunning)

      # Save current status before it is udpated so as to log
      # the status change only if it's value changed for real.
      # see conditional logger.info below
      if 'currentStatus' in newservice['status']:
        previous_status = newservice['status']['currentStatus']
      else:
        previous_status = None

      if allRunning:
        newservice['status'][parent_kind]['status'] = "Running"
        newservice['status']['currentStatus'] = "Running"
      else:
        newservice['status']['currentStatus'] = "Starting"

      current_status = newservice['status']['currentStatus']
      if previous_status != current_status:
        logger.info(f"Service {parent_name} of kind {parent_kind} status updated to {current_status}")

      # Update the K8s resource and graph node properties
      network_api.patch(body=newservice, name=parent_name, namespace=parent_namespace, content_type='application/merge-patch+json')
      update_network_node(newservice, newservice['spec'], parent_namespace, parent_name, kind, newservice['metadata']['uid'])