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
@kopf.on.create('google.dev', 'v1', 'wireguardappliance')
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

    # Use common functions for installation and routing
    mgmt_ip_address, data_ip_address = await get_vm_addresses(namespace, name)
    logger.debug("found mgmt ip address %s (%s, %s, %s)", mgmt_ip_address, kind, name, uid )
    logger.debug("found dataplane ip address %s (%s, %s, %s)", data_ip_address, kind, name, uid )

    peersInfo = await build_peers_info(namespace, spec)
    
    await setup_wireguard_installation(servicename, name, namespace, spec, mgmt_ip_address, data_ip_address, peersInfo)
    await create_wireguard_routes(namespace, name, spec, peersInfo)

    # Get the last peer info for return values (maintaining backward compatibility)
    allowed_cidr = peersInfo[-1]['allowedCidr'] if peersInfo else None
    peer_ip_address = peersInfo[-1]['ipAddress'] if peersInfo else None

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
@kopf.on.update('google.dev', 'v1', 'wireguardappliance', field='status')
async def wireguardappliance_update(body, spec, old, new, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update wireguardappliance {name} with spec: {spec} and status: {status['wireguardappliance']['status']}")
  kind = body.get('kind')
  await update_network_node(body, spec, namespace, name, kind, meta['uid'])

  # if the status.wireguardappliance.status has changed from Running to Failed then re run the installation scripts
  # and set the status back to running. 
  logger.info(f"from old = {old} to new = {new}")

  
##########################################
# Watch for Failed WG
##########################################
@kopf.on.field('google.dev', 'v1', 'wireguardappliance', field='status.wireguardappliance.status')
async def handle_wireguard_status_change(old, new, spec, status, namespace, name, body, **kwargs):
    """
    Handler that watches for status field changes and triggers re-installation
    when status changes from Running to Failed
    """
    logger.info(f"WireGuard status change detected for {name}: {old} -> {new}")
    
    # Check if this is a Running -> Failed transition
    if old == "Running" and new == "Failed":
        logger.warning(f"Detected failure in WireGuard appliance {name}, triggering re-installation...")
        
        try:
            # Update status to indicate re-installation is starting
            await update_wireguard_status(namespace, name, "Reinstalling", "Re-installation triggered due to failure")
            
            # Trigger the installation process
            await trigger_wireguard_reinstallation(spec, namespace, name, body)
            
            # Update status to Running after successful installation
            await update_wireguard_status(namespace, name, "Running", "Re-installation completed successfully")
            
        except Exception as e:
            logger.error(f"Re-installation failed for WireGuard appliance {name}: {e}")
            await update_wireguard_status(namespace, name, "Failed", f"Re-installation failed: {str(e)}")
            raise kopf.TemporaryError(f"Re-installation failed: {e}", delay=60)

##########################################
# Catch updates on status
##########################################
async def trigger_wireguard_reinstallation(spec, namespace, name, body):
    """
    Trigger the re-installation process for WireGuard appliance
    """
    logger.info(f"Starting re-installation for WireGuard appliance {name}")
    
    # Extract servicename from ownerReferences
    servicename = body['metadata']['ownerReferences'][0]['name']
    
    # Use common functions for reinstallation
    mgmt_ip_address, data_ip_address = await get_vm_addresses(namespace, name)
    peersInfo = await build_peers_info(namespace, spec)
    
    await setup_wireguard_installation(servicename, name, namespace, spec, mgmt_ip_address, data_ip_address, peersInfo)
    await create_wireguard_routes(namespace, name, spec, peersInfo)
    
    logger.info(f"Re-installation completed for WireGuard appliance {name}")
   
##########################################
# Patch restart after failure status
##########################################
async def update_wireguard_status(namespace, name, status_value, message):
    """
    Update the status of the WireGuard appliance resource
    """
    import kubernetes
    from datetime import datetime
    
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version="google.dev/v1", kind="WireguardAppliance")
    
    # Get current resource
    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()
    
    # Update status
    if 'status' not in resource_dict:
        resource_dict['status'] = {}
    if 'wireguardappliance' not in resource_dict['status']:
        resource_dict['status']['wireguardappliance'] = {}
    
    resource_dict['status']['wireguardappliance']['status'] = status_value
    resource_dict['status']['wireguardappliance']['message'] = message
    resource_dict['status']['wireguardappliance']['lastUpdated'] = datetime.utcnow().isoformat()
    
    # Patch the resource
    api.patch(
        body=resource_dict, 
        name=name, 
        namespace=namespace, 
        content_type='application/merge-patch+json'
    )

#########################################################################
# Common helper functions to eliminate code duplication
#########################################################################

async def get_vm_addresses(namespace, name):
    """
    Get management and dataplane IP addresses for a VM
    """
    mgmt_ip_address = await get_ip(namespace, name)
    data_ip_address = await get_ip(namespace, name, networkname="dataplane")
    if mgmt_ip_address is None or data_ip_address is None:
        raise kopf.TemporaryError("No ip address found on VM yet, temporary error - waiting", 10)
    
    logger.debug("found mgmt ip address %s", mgmt_ip_address)
    logger.debug("found dataplane ip address %s", data_ip_address)
    
    return mgmt_ip_address, data_ip_address

async def build_peers_info(namespace, spec):
    """
    Build the peers information array with subnet info and IP addresses
    """
    peersInfo = []
    for peer in spec['peers']:
        # copy the base peer info and add to it
        peerInfo = peer.copy()
        subnet_info = await get_subnet_info(namespace, peer['allowedInterface']['name'])
        allowed_cidr = subnet_info.get('spec')['ipCidrRange']
        logger.debug("allowed cidr %s", allowed_cidr)
        peerInfo['allowedCidr'] = allowed_cidr

        # discover the peer ip address
        peer_ip_address = await get_ip(namespace, peer['peerName'], networkname="dataplane")
        logger.debug("found peer dataplane ip address %s", peer_ip_address)
        peerInfo['ipAddress'] = peer_ip_address
        peersInfo.append(peerInfo)
    
    return peersInfo

async def setup_wireguard_installation(servicename, name, namespace, spec, mgmt_ip_address, data_ip_address, peersInfo):
    """
    Handle the WireGuard VPN installation
    """
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

async def create_wireguard_routes(namespace, name, spec, peersInfo):
    """
    Create routes from source interface to all allowed interfaces
    """
    logger.info("setting up routes")
    for peer in peersInfo:
        logger.info("Creating route from %s to %s", spec.get('sourceInterface'), peer['allowedInterface'])
        await create_route(namespace, name, spec.get('sourceInterface'), peer['allowedInterface'])
