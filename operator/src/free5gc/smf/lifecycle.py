import logging
from utils.compute import get_resource_api
import utils.constants as constants
import kopf
from free5gc.smf.lifecycle_tasks import getUPFAddress, template_smf_manifest
from free5gc.utils.k8s import getClusterIP
from ruamel.yaml import YAML
import kubernetes

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'sessionmanagementfunction')
async def smf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create smf {name} with spec: {spec}")

  # get the ip address of the named cluster
  ip = await getClusterIP(spec.get("clusterName"))
  logger.debug("Cluster Public endpoint %s",ip)

  # get the ip address of the spec named UPF
  upfname = spec.get("upf").get("name")
  upfnamespace = spec.get("upf").get("namespace")
  upfaddress=await getUPFAddress(upfname, upfnamespace)

  # render the smf manifests
  smf_files=["smf-configmap.yaml","smf-deployment.yaml","smf-service.yaml"]
  for f in smf_files:
    manifest = await template_smf_manifest(constants.basedir+"/free5gc/smf/templates/",
                                          f,
                                          upfname,
                                          upfaddress,
                                          spec.get('dnn').get('cidr'),
                                          spec.get('dnn').get('static_cidr'),
                                          spec.get('dnn').get('gateway_address'),
                                          spec.get('dnn').get('destination_ip'),
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
