# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import kopf
import logging
from utils.compute import *
from vpn.wireguard.lifecycle_tasks import *
from graph.lifecycle_tasks import update_network_node

# https://wireguard.how/server/google-cloud-platform/
# https://ubuntu.com/server/docs/wireguard-vpn-site-to-site

logger = logging.getLogger(__name__)

#########################################################################
# Create a Wireguard virtual network appliance
#########################################################################
@kopf.on.create('wireguardappliance')
async def wireguardappliance(body,spec, name, namespace, uid, logger, **kwargs):
    logger.debug(f"A wireguard handler is called with spec: {spec}")

    servicename = body['metadata']['ownerReferences'][0]['name']
    kind = body.get('kind')

    # create children resources in the automation namespace
    await create_compute(namespace,
                        None, # parent name
                        name, # vmname
                        None, # external ip
                        [spec.get('sourceInterface')],
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        vpn=True,
                        monitor=False, # set to false so this VM is not scraped by prometheus
                        graph=True)

    # Get mgmt and dataplane address for the VM created above
    mgmt_ip_address = await get_ip(namespace, name)
    data_ip_address = await get_ip(namespace, name, networkname="dataplane")
    if mgmt_ip_address is None or data_ip_address is None:
        raise kopf.TemporaryError("No ip address found on VM yet, temporary error - waiting",10)

    logger.debug("found mgmt ip address %s (%s, %s, %s)", mgmt_ip_address, kind, name, uid )
    logger.debug("found dataplane ip address %s (%s, %s, %s)", data_ip_address, kind, name, uid )

    # find the allowed interface cidr and VM ip address for each of the peers
    peersInfo=[]
    for peer in spec['peers']:
        # copy the base peer info and add to it
        peerInfo=peer
        subnet_info = await get_subnet_info(namespace, peer['allowedInterface']['name'])
        allowed_cidr = subnet_info.get('spec')['ipCidrRange']
        logger.debug("allowed cidr %s", allowed_cidr)
        peerInfo['allowedCidr']=allowed_cidr

        # discover the peer ip address
        peer_ip_address = await get_ip(namespace, peer['peerName'], networkname="dataplane")
        logger.debug("found peer dataplane ip address %s", peer_ip_address)
        peerInfo['ipAddress']=peer_ip_address
        peersInfo.append(peerInfo)

    # Run ansible to install software on the VM
    await install_vpn(
                servicename,
                name,
                mgmt_ip_address,
                data_ip_address,
                spec.get('tunnelAddress'),
                spec.get('tunnelSubnet'),
                spec.get('keys'),
                peersInfo     
            )

    # once the vpn is running create the route from source to allowed interface
    # loop over all allowed interfaces and create routes from source interface
    logger.info("setting up routes now")
    for peer in peersInfo:
        logger.info("Creating route from %s to %s",spec.get('sourceInterface'), peer['allowedInterface'])
        await create_route(namespace, name, spec.get('sourceInterface'), peer['allowedInterface'])

    return {
        "status":"Running", 
        "mgmt_ip_address": mgmt_ip_address, 
        "data_ip_address": data_ip_address, 
        "allowed_cidr": allowed_cidr,
        "peer_data_ip_address": peer_ip_address
    }

##########################################
# Catch updates on status
##########################################
@kopf.on.update('wireguardappliance', field='status')
async def wireguardappliance_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update wireguardappliance {name} with spec: {spec} and status: {status['wireguardappliance']['status']}")
  kind = body.get('kind')
  await update_network_node(body, spec, namespace, name, kind, meta['uid'])