import logging
from utils.compute import *
import kopf
from free5gc.upf.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'userplanefunction')
async def upf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create upf {name} with spec: {spec}")

  # get the VPC name to bind UPF to
  network_interface = spec.get('interface')
  if network_interface is None:
    raise kopf.PermanentError("No interface found")

  # create UPF VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        network_interface, # set this to the target network name to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        release="ubuntu-2004-lts",
                        monitor=False) # set to false so this VM is not scraped by prometheus

  # install UPF to VM 
  # await run_install(namespace, name)

  return {
      "status":"Running", 
  }
