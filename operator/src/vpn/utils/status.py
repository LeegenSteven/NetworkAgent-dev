import logging
from utils.k8s import *

logger = logging.getLogger(__name__)

############################################
# Monitor service children and update status
############################################
@kopf.on.event('google.dev', 'v1', 'WireguardAppliance')
def servicestatus(event,meta,namespace,status, **_):
    parent_name = meta['labels']['kex-parent-name']
    parent_namespace = meta['labels']['kex-parent-namespace']
    parent_kind = meta['labels']['kex-parent-kind']
    name = meta['name']

    logger.debug("++++++++++++++++++++Wireguard Change Event++++++++++++++++++++++++")
    logger.debug("Parent name = %s", parent_name)
    logger.debug("Parent namespace = %s", parent_namespace)
    logger.debug("Parent kind = %s", parent_kind)
    logger.debug("Wireguard name = %s", name)

    try:

      updateStatus(namespace,parent_kind, parent_name, parent_namespace, status, event, name)

    except kubernetes.client.rest.ApiException as e:
      if e.status == 404:
        logger.debug("No VPN Service Found")
      elif e.status == 409:
        logger.debug("Conflict - manifest is out of date - reload and  try again!!!!!!!!!!!!")
        updateStatus(namespace, parent_kind, parent_name, parent_namespace, status, event, name)
      else:
        logger.error(e)

def updateStatus(namespace, parent_kind, parent_name, parent_namespace, status, event, name):
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
                edge['status']="Running"
                break

        logger.debug("====NEW STATUS ====")
        logger.debug(json.dumps(newservice['status'], indent=4))

        allRunning = True
        for edge in newservice['status'][parent_kind]['edges']:
          if edge['status'] != "Running":
            allRunning = False

        logger.debug("-------------------------------ALL RUNNING = %s------------------------------", allRunning)

        if allRunning:
          newservice['status'][parent_kind]['status']="Running"
          newservice['status']['currentStatus']="Running"
        else:
          newservice['status']['currentStatus']="Starting"

        network_api.patch(body=newservice, name=parent_name, namespace=parent_namespace, content_type='application/merge-patch+json')
