import kubernetes
import kopf
import logging
from free5gc.utils.k8s import get_api_client, getClusterDetails
from jinja2 import Environment, FileSystemLoader
import os

logger = logging.getLogger(__name__)

##########################################################
# template the smf manifests
##########################################################
async def template_smf_manifest(folder, filename,upfname,upfip,dnn_cidr,dnn_static_cidr,dnn_gateway_address,dnn_destination_ip):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT"),
        UPFNAME=upfname,
        UPFADDRESS=upfip,
        DNN_CIDR=dnn_cidr,
        DNN_STATIC_CIDR=dnn_static_cidr,
        DNN_GATEWAY_ADDRESS=dnn_gateway_address,
        DNN_DESTINATION_IP=dnn_destination_ip
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