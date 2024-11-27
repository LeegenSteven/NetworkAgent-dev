import logging
from utils.compute import *
import kopf
from free5gc.nginx.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'nginx')
async def upf(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create nginx {name} with spec: {spec}")

  # get the VPC name to bind UPF to
  network_interface = spec.get('interface')
  if network_interface is None:
    raise kopf.PermanentError("No interface found")

  # create UPF VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        [network_interface], # set this to the target network name to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        release="ubuntu-2004-lts",
                        monitor=False) # set to false so this VM is not scraped by prometheus

  # install UPF to VM 
  await run_install(namespace, name)
  ip=await get_ip(namespace, name, network_interface.get('name'))

  return {
      "status":"Running",
      "address": ip
  }
