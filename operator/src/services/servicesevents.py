import kopf
import logging
import services.sitetosite.lifecycle as sitetosite

logger = logging.getLogger(__name__)

@kopf.on.create('connectivityservice')
async def connectivityservice(spec, name, namespace, logger, **kwargs):
    logger.info(f"Create connectivityservice service handler is called with spec: {spec}")

    if 'type' not in spec or 'interfaces' not in spec:
        raise kopf.PermanentError("fields 'type' and 'interfaces' must be provided.")

    if spec.get("type") != "site-to-site":
        raise kopf.PermanentError("Only site-to-site supported.")

    if len(spec.get('interfaces'))!=2:
        raise kopf.PermanentError("Two interfaces must be provided.")

    return await sitetosite.create(spec)
