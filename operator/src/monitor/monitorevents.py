import kopf
import ansible_runner
import os
import logging

logger = logging.getLogger(__name__)

@kopf.on.create('monitoring')
async def create_monitoring_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create monitoring service handler is called with spec: {spec}")

 