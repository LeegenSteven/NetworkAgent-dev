import logging

logger = logging.getLogger(__name__)

##########################################
# Create a new Point To Point VPN
##########################################
@kopf.on.create('google.dev', 'v1', 'firewall')
async def firewall(spec, status, name, logger, **kwargs):
  logger.debug(f"Create firewall {name} with spec: {spec}")
