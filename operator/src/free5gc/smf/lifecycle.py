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
from utils.compute import get_resource_api
import utils.constants as constants
import kopf
from free5gc.smf.lifecycle_tasks import getUPFAddress, template_smf_manifest, getLoadBalancerIP
from free5gc.utils.k8s import getClusterIP
from ruamel.yaml import YAML
import kubernetes

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunctionget_resource_api
##########################################
@kopf.on.create('google.dev', 'v1', 'sessionmanagementfunction')
async def smf(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create smf {name} with spec: {spec}")

  # get the ip address of the named cluster
  ip = await getClusterIP(spec.get("clusterName"))
  logger.info("Cluster Public endpoint %s",ip)

  # get the ip address of the spec named UPF
  upfname = spec.get("upf").get("name")
  upfnamespace = spec.get("upf").get("namespace")
  upfaddress=await getUPFAddress(upfname, upfnamespace)

  # get the ip address for london cluster load balancer from the networkautomation cluster
  lbIp = await getLoadBalancerIP()

  # render the smf manifests
  smf_files=["smf-configmap.yaml","smf-deployment.yaml","smf-service.yaml"]
  for f in smf_files:
    manifest = await template_smf_manifest(constants.basedir+"/free5gc/smf/templates/",
                                          f,
                                          upfname,
                                          upfaddress,
                                          lbIp
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
