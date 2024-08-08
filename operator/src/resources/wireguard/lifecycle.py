import kopf
import logging
from resources.wireguard.lifecycle_tasks import *

# https://wireguard.how/server/google-cloud-platform/
# https://ubuntu.com/server/docs/wireguard-vpn-site-to-site

logger = logging.getLogger(__name__)

@kopf.on.create('wireguardappliance')
async def create(spec, name, namespace, logger, **kwargs):
    logger.info(f"A handler is called with spec: {spec}")

    vmname = spec.get('vmname')
    if not vmname:
        raise kopf.PermanentError(f"edgevm must be set. Got {vmname!r}.")

    # discover the VM k8s object and get its public ip address
    vm_info=await get_vm_info(vmname)

    # find the interface with an external address
    external_address_name=None

    interfaces = vm_info.spec.get('networkInterface')
    for int in interfaces:
        accessConfig = int.get('accessConfig')
        if accessConfig is not None:
            external_address_name =int['accessConfig'][0]['natIpRef']['name']
            break

    if external_address_name == None:
        raise kopf.TemporaryError("No external address found on VM yet - waiting")

    logger.info("found external address name %s", external_address_name )

    # find the IP address of the external interface
    address_info = await get_address_info(external_address_name)
    external_ip_address = address_info.spec['address']
    logger.info("found external ip address %s", external_ip_address)

    # discover the allowed interface's cidr
    subnet_info = await get_subnet_info(spec.get('allowedInterface'))
    allowed_cidr = subnet_info.get('spec')['ipCidrRange']
    logger.info("allowed cidr %s", allowed_cidr)

    # discover the peer k8s VM object
    peer_vm_info = await get_vm_info(spec.get('peer'))
    interfaces = peer_vm_info.spec.get('networkInterface')

    peer_address_name=None
    for int in interfaces:
        accessConfig = int.get('accessConfig')
        if accessConfig is not None:
            peer_address_name =int['accessConfig'][0]['natIpRef']['name']
            break

    if peer_address_name == None:
        raise kopf.TemporaryError("No external address found on Peer VM yet - waiting")

    logger.info("found peer external address name %s", peer_address_name )

    # find the IP address of the peer external interface
    peer_address_info = await get_address_info(peer_address_name)
    peer_ip_address = peer_address_info.spec['address']
    logger.info("found peer external ip address %s", peer_ip_address)

    # Run ansible to install software on the VM
    await install_vpn(
                spec.get('vmname'),
                external_ip_address,
                spec.get('tunnelAddress'),
                spec.get('tunnelSubnet'),
                allowed_cidr,
                spec.get('peer'),
                peer_ip_address
            )

    return {
        "external_ip_address": external_ip_address, 
        "allowed_cidr": allowed_cidr,
        "peer_ip_address": peer_ip_address
    }

