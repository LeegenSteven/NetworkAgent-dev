import kopf
import logging
from resources.wireguard.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('connectivitytest')
async def test(spec, name, namespace, logger, **kwargs):
    logger.info("Create test case")
    