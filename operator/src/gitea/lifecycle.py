import kopf
import logging
from utils.compute import *
from gitea.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','gitea')
async def create_gitea(spec, name, namespace, logger, **kwargs):
    logger.debug("Create gitea repo")

    # Create external IP address
    await create_external_ip(namespace, "gitea", os.getenv("GOOGLE_REGION"), graph=False)
    external_ip_address = await get_external_ip_address(namespace, "gitea")

    # Create VM and attach IP address
    await create_compute(namespace, 
                         name,
                         "gitea",
                         external_ip_address, # replace with None when only private IP address
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         os.getenv("WEBAPPS_PWD"), 
                         monitor=False, # set to false so this VM is not scraped by prometheus
                         graph=False) # set to false so this VM is not showing on topology graph

    # Install Gitea
    await run_gitea_install(namespace, external_ip_address)

    # Create the private key
    # await create_root_sync_private_key()

    # # Create root sync object
    await create_root_sync(external_ip_address)

    return {"status": "Running", "external_ip_address": external_ip_address}

