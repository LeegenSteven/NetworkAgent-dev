import kubernetes
import kopf
import logging
from utils.compute import get_ip_address
from free5gc.utils.k8s import get_api_client, getClusterDetails
from jinja2 import Environment, FileSystemLoader
import os

logger = logging.getLogger(__name__)

##########################################################
# get the load balancer IP address
##########################################################
async def getLoadBalancerIP():
    logger.info("Get load balancer IP address for london-cluster")
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
async def template_smf_manifest(folder, filename,upfname,upfip, lbip):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        UPFNAME=upfname,
        UPFADDRESS=upfip,
        LOADBALANCERIP=lbip
        )
    return output

##########################################################
# Return the ip address of the UPF named
##########################################################
async def getUPFAddress(upfname, upfnamespace):
    logger.debug("get upf address for %s %s", upfname, upfnamespace)
    # look up the UPF and get its IP address from the networkautomation cluster
    cluster=await getClusterDetails("networkautomation")
    logger.debug(cluster)

    if cluster is None:
        raise kopf.PermanentError("No networkautation cluster found")

    networkautomation_api_client= get_api_client(cluster.endpoint, cluster.master_auth.cluster_ca_certificate)

    client = kubernetes.dynamic.DynamicClient(networkautomation_api_client)
    network_api = client.resources.get(api_version="google.dev/v1", kind="UserPlaneFunction")
    logger.debug("looking for upf %s %s", upfname, upfnamespace)

    upfaddress=None
    # get the ip address of the UPF
    try:
        result = network_api.get(name=upfname, namespace=upfnamespace)
        logger.debug(result)
        if result.get('status').get('upf') is None:
            raise kopf.TemporaryError("Waiting for upf to come up")
        upfaddress=result.get('status').get('upf').get('ingressAddress')
        logger.debug("UPF ADDRESS = %s", upfaddress)

    except kubernetes.client.rest.ApiException as e: 
        logger.info(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No UPF {upfname} found yet. Waiting...")

    return upfaddress