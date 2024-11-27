import logging
from utils.compute import *
import kopf
from free5gc.utils.k8s import getClusterIP
from free5gc.amf.lifecycle_tasks import template_amf_manifest
from ruamel.yaml import YAML
import kubernetes

logger = logging.getLogger(__name__)

##########################################
# Create a new amf
##########################################
@kopf.on.create('google.dev', 'v1', 'accessmanagementfunction')
async def amf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create amf {name} with spec: {spec}")

  # get the external addres of the cluster
  ip = await getClusterIP(spec.get("clusterName"))

  # render the amf manifests
  amf_files=["amf-configmap.yaml","amf-deployment.yaml","amf-service.yaml"]
  for f in amf_files:
    manifest = await template_amf_manifest(constants.basedir+"/free5gc/amf/templates/",
                                          f,
                                          ip
                                          )
    logger.debug(manifest)

    # deploy the manifest to the current cluster
    yaml = YAML(typ='safe', pure=True)
    manifest_yaml = yaml.load(manifest)
    logger.debug(manifest_yaml)

    # adopt children
    kopf.adopt(manifest_yaml)

    network_api = get_resource_api("v1", manifest_yaml.get('kind'))
    try:
      network_api.create(body=manifest_yaml, namespace=namespace)
    except kubernetes.client.rest.ApiException as e: 
      logger.debug(e.status)
      if e.status == 422:
        raise kopf.PermanentError("Unprocessable entity.")
      elif e.status == 409:
        logger.debug("already exists - skipping")
      else:
        logger.debug(e)

  return {
      "status":"Running", 
      "address": ip
  }
