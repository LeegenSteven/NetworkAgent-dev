import logging
from langchain_core.tools import tool
from kubernetes import client, config

logger = logging.getLogger(__name__)

# https://github.com/kubernetes-client/python/blob/master/examples/namespaced_custom_object.py

@tool
def getServiceInfo(customerName: str)->str:
    """
    Retrieve a customers connectivity service
    Args:
        customerName(str): The company name of the customer requesting service information
    Returns:
        str: YAML string representing the full information
    """
    logger.info("Getting service info for customer %s",customerName)

    config.load_kube_config()

    api = client.CustomObjectsApi()

    # get the resource and print out data
    resource = api.get_namespaced_custom_object(
        group="google.dev",
        version="v1",
        name="edge1",
        namespace="networkautomation",
        plural="edgeappliances",
    )

    logger.info(resource)

# @tool
def createNewService():
    pass

# @tool
def updateService():
    pass
