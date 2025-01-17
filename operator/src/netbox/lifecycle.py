import kopf
import logging
from utils.compute import *
from netbox.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','netbox')
async def create_netbox(spec, name, namespace, logger, **kwargs):
    logger.debug("Create netbox instance")

    # Create external IP address
    await create_external_ip(namespace, "netbox", os.getenv("GOOGLE_REGION"))
    external_ip_address = await get_ip_address(namespace, "netbox")

    # Create VM and attach IP address
    await create_compute(namespace, 
                         name,
                         "netbox",
                         external_ip_address, # replace with None when only private IP address
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         monitor=False) # set to false so this VM is not scraped by prometheus

    # Install Gitea
    await run_netbox_install(namespace, external_ip_address)

    return {"status": "Running", "external_ip_address": external_ip_address}

