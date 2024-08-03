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

    cwd = os.getcwd()
    pdir = cwd+"/resources/wireguard/playbooks"
    logger.info("path = %s", pdir)

    # discover the VM k8s object
    get_vm_object(pdir, events.get_vm_event_handler, vmname)

    # discover the allowed interface
    get_allowed_interface(pdir, spec.get('allowedInterface'))

    # discover the peer k8s object
    get_oeer_object(pdir, events, spec.get('peer'))

    # get the public ip address of the VM 
    get_public_ip(pdir, events)

    install(pdir, events, spec)

    return {"status": "running"}

# @kopf.on.update('wireguardappliance')
# def update_wireguard_instance(spec, name, namespace, logger, **kwargs):
#     logger.info("update called")


# @kopf.on.delete('wireguardappliance')
# def delete_wireguard_instances(spec, **_):
#     logger.info("delete called")