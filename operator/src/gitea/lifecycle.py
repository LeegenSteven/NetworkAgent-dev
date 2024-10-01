import kopf
import logging
from utils.compute import *
from gitea.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','gitea')
async def create_gitea(spec, name, namespace, logger, **kwargs):
    logger.debug("Create gitea repo")

    # Create external IP address
    await create_external_ip("gitea", os.getenv("GOOGLE_REGION"))
    external_ip_address = await get_external_ip_address("gitea")

    # Create VM and attach IP address
    await create_compute(name,
                         "gitea",
                         external_ip_address, # replace with None when only private IP address
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         monitor=False) # set to false so this VM is not scraped by prometheus

    # Install Gitea
    await run_gitea_install(external_ip_address)

    return {"status": "Running", "external_ip_address": external_ip_address}

