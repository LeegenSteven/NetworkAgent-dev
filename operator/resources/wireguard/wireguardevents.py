import kopf
import ansible_runner
import os
import logging
from resources.wireguard.utils.discover import *
import asyncio

# https://wireguard.how/server/google-cloud-platform/
# https://ubuntu.com/server/docs/wireguard-vpn-site-to-site

logger = logging.getLogger(__name__)

@kopf.on.create('wireguardappliance')
async def create_wireguard_instance(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    events=WireguardEvents()

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    # discover the VM k8s object and get its public ip address
    get_vm_info( events, vmname)

    # discover the allowed interface's cidr
    get_allowed_interface( events, spec.get('allowedInterface'))

    # discover the peer k8s object
    get_peer_info(events, spec.get('peer'))

    install( events, spec)

    return {"status": "running"}

