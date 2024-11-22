import logging
from utils.compute import *
import kopf

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'accessmanagementfunction')
async def upf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create amf {name} with spec: {spec}")

  # get the ip address of the cluster provided

  return {
      "status":"Running", 
      "externalIP": ""
  }
