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
from free5gc.utils.k8s import get_api_client, getAutomationClusterDetails, getClusterDetails
from jinja2 import Environment, FileSystemLoader
import os
import utils.constants as constants
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

##########################################################
# get the load balancer IP address
##########################################################
async def getLoadBalancerIP():
    logger.debug("Get load balancer IP address for london-cluster")
    cluster = await getClusterDetails("networkautomation")
    if cluster is None:
        raise kopf.PermanentError("No networkautation cluster found")

    # get the ip address of the shared loadbalancer compute ip
    networkautomation_api_client= get_api_client(cluster.endpoint, cluster.master_auth.cluster_ca_certificate)
    lbIp = await get_ip_address("london", "london-cluster-ip-lb", networkautomation_api_client)
    return lbIp

##########################################################
# template the smf manifests
##########################################################
async def template_smf_manifest(folder, filename, upfname ,upfip, podip):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        UPFNAME=upfname,
        UPFADDRESS=upfip,
        SMF_POD_IP=podip
        )
    return output

##########################################
# Create a new GKR IP Route for address
##########################################
async def createGKEIPRoute(namespace, podip):
  logger.info("Creating GKE Route for SMF")
  yaml = YAML(typ='safe', pure=True)

  gkeip_manifest = await template_smf_manifest(
     constants.basedir+"/free5gc/smf/templates/", 
     "smf-ip.yaml",
     "",
     "",
     podip)

  gkeip_manifest_yaml = yaml.load(gkeip_manifest)
  kopf.adopt(gkeip_manifest_yaml)

  try:
    network_api = get_resource_api("v1", gkeip_manifest_yaml.get('kind'))
    network_api.create(body=gkeip_manifest_yaml, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("gkeip exists already - skipping")
    else:
      logger.error(e)


##########################################
# Create a new SMF config map
##########################################
async def createSMFConfigMap(namespace, upfname, upfip, podip):
  logger.debug("Creating config map for SMF")
  yaml = YAML(typ='safe', pure=True)

  configmap_manifest = await template_smf_manifest(
     constants.basedir+"/free5gc/smf/templates/", 
     "smf-configmap.yaml",
     upfname,
     upfip,
     podip)

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

##########################################################
# Return the ip address of the UPF named
##########################################################
async def getUPFAddress(upfname, upfnamespace):
    logger.debug("get upf address for %s %s", upfname, upfnamespace)
    # look up the UPF and get its IP address from the networkautomation cluster
    cluster=await getAutomationClusterDetails()
    logger.debug(cluster)

    if cluster is None:
        raise kopf.PermanentError("No networkautomation cluster found")

    networkautomation_api_client= get_api_client(cluster.endpoint, cluster.master_auth.cluster_ca_certificate)

    client = kubernetes.dynamic.DynamicClient(networkautomation_api_client)
    network_api = client.resources.get(api_version="google.dev/v1", kind="UserPlaneFunction")
    logger.debug("looking for upf %s %s", upfname, upfnamespace)

    upfaddress=None
    # get the ip address of the UPF
    try:
        result = network_api.get(name=upfname, namespace=upfnamespace)
        logger.debug(result)
        if result.get('status').get('userplanefunction') is None:
            raise kopf.TemporaryError("Waiting for upf to come up")
        upfaddress=result.get('status').get('userplanefunction').get('ingressAddress')
        logger.debug("UPF ADDRESS = %s", upfaddress)

    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No UPF {upfname} found yet. Waiting...")

    return upfaddress