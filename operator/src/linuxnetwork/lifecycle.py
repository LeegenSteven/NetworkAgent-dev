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
import kubernetes
from linuxnetwork.lifecycle_tasks import (
    create_linux_network,
    delete_linux_network,
    get_network_status
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# DockerNetwork Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'linuxnetwork')
async def create_linuxnetwork(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle LinuxNetwork creation using Ansible"""
    logger.info(f"Creating LinuxNetwork: {name} in namespace: {namespace}")
    logger.info(f"spec {spec}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        # Update status to indicate creation has started
        await update_status(name, namespace, "Creating", "Creating Linux network")

        # Create the Linux network using Ansible
        result = await create_linux_network(ip_address, spec)

        if result['success']:
            # if network_type == 'management' also add the default_interface to status
            if spec.get('network_type') == 'management':
                logger.info("Linux network is of type 'management', performing additional setup")

                # Extract default interface if available
                default_interface = result.get('default_interface', 'unknown')
                interface_ip = result.get('interface_ip', 'unknown')
                default_gateway = result.get('default_gateway', 'unknown')
                extra_status = {'interface': default_interface, 'gateway': default_gateway, 'interface_ip': interface_ip}
            else:
                extra_status = {}
            
            # Update status to ready with full details
            await update_status(
                name, namespace, "Ready", 
                f"Linux network {spec['name']} created successfully",
                extra_status=extra_status
            )
            logger.info(f"Successfully created LinuxNetwork {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to create network: {result['error']}")
            raise kopf.PermanentError(f"Linux network creation failed: {result['error']}")

    except Exception as e:
        logger.error(f"Failed to create LinuxNetwork {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.delete('google.dev', 'v1', 'linuxnetwork')
async def delete_linuxnetwork(body, spec, status, name, namespace, logger, **kwargs):
    """Handle LinuxNetwork deletion using Ansible"""
    logger.info(f"Deleting LinuxNetwork: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    logger.info("TODO::make sure all dependent resources are deleted first")

    try:        
        # Delete the Docker network using Ansible
        result = await delete_linux_network(ip_address, spec, status)
        
        if result['success']:
            logger.info(f"Successfully deleted DockerNetwork {spec.get('network_name')}")
        else:
            logger.warning(f"Failed to delete Docker network {spec.get('network_name')}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during DockerNetwork deletion {name}: {e}")
        # Don't raise error on delete failure

# @kopf.on.field('google.dev', 'v1', 'linuxnetwork', field='status.phase')
async def monitor_linuxnetwork(old, new, body, spec, name, namespace, logger, **kwargs):
    """Monitor LinuxNetwork status and update accordingly"""

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    if new == "Ready":
        # Periodically check network status
        try:
            network_name = spec.get('name', name)
            status = await get_network_status(ip_address, network_name)
            
            if not status['exists']:
                await update_status(name, namespace, "Failed", "Linux network no longer exists")
                logger.warning(f"LinuxNetwork {name} no longer exists in Docker")
                
        except Exception as e:
            logger.error(f"Failed to check network status for {name}: {e}")

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, extra_status: dict = None):
    """Update the status of a LinuxNetwork resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')

    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()

    if 'status' not in resource_dict:
        resource_dict['status'] = {}

    status = {
        'phase': phase,
        'message': message
    }
    
    # Add any additional status fields
    if extra_status:
        status.update(extra_status)

    logger.info(f"Updating status for LinuxNetwork {name}: {status}")

    resource_dict['status'].update(status)

    logger.info(resource_dict['status'])

    try:
        api.patch(
            namespace=namespace,
            name=name,
            body=resource_dict,
            content_type='application/merge-patch+json',
            subresource='status'
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for LinuxNetwork {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for LinuxNetwork {name}: {e}")
