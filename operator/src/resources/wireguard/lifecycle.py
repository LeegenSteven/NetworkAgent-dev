import kopf
import logging
from utils.compute import *
from resources.wireguard.lifecycle_tasks import *

# https://wireguard.how/server/google-cloud-platform/
# https://ubuntu.com/server/docs/wireguard-vpn-site-to-site

logger = logging.getLogger(__name__)

@kopf.on.create('wireguardappliance')
async def create(body,spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    servicename = body['metadata']['ownerReferences'][0]['name']

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    
    mgmt_ip_address = await get_ip(vmname)
    data_ip_address = await get_ip(vmname, networkname="dataplane")
    if mgmt_ip_address is None or data_ip_address is None:
        raise kopf.TemporaryError("No ip address found on VM yet - waiting")

    logger.info("found mgmt ip address %s", mgmt_ip_address )

    # discover the allowed interface's cidr
    subnet_info = await get_subnet_info(spec.get('allowedInterface'))
    allowed_cidr = subnet_info.get('spec')['ipCidrRange']
    logger.info("allowed cidr %s", allowed_cidr)

    # discover the peer ip address
    peer_ip_address = await get_ip(spec.get('peer'), networkname="dataplane")
    logger.info("found peer external ip address %s", peer_ip_address)

    # Run ansible to install software on the VM
    await install_vpn(
                servicename,
                spec.get('vmname'),
                mgmt_ip_address,
                data_ip_address,
                spec.get('tunnelAddress'),
                spec.get('tunnelSubnet'),
                allowed_cidr,
                spec.get('peer'),
                peer_ip_address,
                spec.get('keys'),
                spec.get('peerKeys')     
            )

    return {
        "mgmt_ip_address": mgmt_ip_address, 
        "data_ip_address": data_ip_address, 
        "allowed_cidr": allowed_cidr,
        "peer_data_ip_address": peer_ip_address
    }

