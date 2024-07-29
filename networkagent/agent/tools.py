import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

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

def createNewService():
    pass

def updateService():
    pass
