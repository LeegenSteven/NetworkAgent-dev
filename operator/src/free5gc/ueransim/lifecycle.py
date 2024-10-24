import logging
import kopf
from utils.compute import *
from free5gc.ueransim.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a new ueransim
##########################################
@kopf.on.create('google.dev', 'v1', 'ueransim')
async def ueransim(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create ueransim {name} with spec: {spec}")

  # get the VPC name to bind UPF to
  network_interface = spec.get('interface')
  logger.debug("Network %s found", network_interface)
  if network_interface is None:
    raise kopf.PermanentError("No interface found")

  # create UERANSIM VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name, # vm name
                        None, # external IP
                        network_interface, # set this to the target network name to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        release="ubuntu-2004-lts",
                        monitor=False) # set to false so this VM is not scraped by prometheus

  # install UERANSIM to VM 
  await run_install(namespace, name)

  return {
      "status":"Running", 
  }

