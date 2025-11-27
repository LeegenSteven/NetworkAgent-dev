import kopf
import logging
from typing import Dict, Any, Optional
import kubernetes
from vyosrouter.lifecycle_tasks import (
    create_vyos_router,
    delete_vyos_router,
    update_vyos_router,
    configure_vyos_router,
    check_linux_networks_ready
)

logger = logging.getLogger(__name__)

#########################################################################
# VyOSRouter Lifecycle Management
#########################################################################

@kopf.on.create('guardian.dev', 'v1', 'vyosrouter')
async def create_vyosrouter(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSRouter creation using Ansible"""
    logger.info(f"Creating VyOSRouter: {name} in namespace: {namespace}")

    # Update status to indicate pending
    await update_status(name, namespace, "Pending", "Validating and waiting for dependencies")

    # Extract router configuration from spec
    router_config = {
        'name': name,
        'hostname': spec['hostname'],
        'router_id': spec['router_id'],
        'image': spec.get('image', 'vyos:1.5'),
        'source_network': spec.get('source_network'),
        'interfaces': spec.get('interfaces', []),
        'vrfs': spec.get('vrfs', []),
        'protocols': spec.get('protocols', {}),
        'services': spec.get('services', {}),
        'qos': spec.get('qos', {}),
        'firewall': spec.get('firewall', {})
    }

    logger.info(f"VyOSRouter config: {router_config}")

    # Check if all required LinuxNetwork CRs are ready
    interfaces = router_config.get('interfaces', [])
    if interfaces:
        all_ready, not_ready_networks = await check_linux_networks_ready(interfaces, namespace)
        
        if not all_ready:
            # Build detailed error message
            network_details = []
            for net in not_ready_networks:
                network_details.append(f"{net['name']} (phase: {net['phase']}, message: {net['message']})")
            
            error_msg = f"Waiting for LinuxNetwork(s) to be ready: {', '.join(network_details)}"
            logger.warning(f"VyOSRouter {name}: {error_msg}")
            
            await update_status(name, namespace, "Pending", error_msg)
            
            # Raise temporary error to retry later
            raise kopf.TemporaryError(error_msg, delay=20)
        
    try:
        # Update status to indicate creation has started
        await update_status(name, namespace, "Creating", "Creating VyOS router container")
        
        # Create the VyOS router container using Ansible
        result = await create_vyos_router(router_config)
        
        if result['success']:
            # Update status to configuring
            await update_status(
                name, namespace, "Configuring", 
                "Applying VyOS configuration",
                container_id=result.get('container_id')
            )
            
            # Configure the VyOS router
            config_result = await configure_vyos_router(router_config)
            
            if config_result['success']:
                # Initialize interface status from spec
                interfaces_status = []
                for iface in router_config['interfaces']:
                    interfaces_status.append({
                        'name': iface['name'],
                        'status': 'up',  # Assume up initially
                        'linux_network': iface.get('linux_network', '')
                    })
                
                # Update status to running with interface details
                await update_status(
                    name, namespace, "Running", 
                    f"VyOS router {name} is running and configured",
                    container_id=result.get('container_id'),
                    ip_address=result.get('management_ip'),
                    interfaces=interfaces_status
                )
                logger.info(f"Successfully created and configured VyOSRouter {name}")
            else:
                await update_status(name, namespace, "Failed", f"Configuration failed: {config_result['error']}")
                raise kopf.PermanentError(f"VyOS router configuration failed: {config_result['error']}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to create container: {result['error']}")
            raise kopf.PermanentError(f"VyOS router creation failed: {result['error']}")
    except Exception as e:
        logger.error(f"Failed to create VyOSRouter {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.update('guardian.dev', 'v1', 'vyosrouter')
async def update_vyosrouter(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSRouter updates using Ansible"""
    logger.info(f"Updating VyOSRouter: {name} in namespace: {namespace}")
    
    try:
        await update_status(name, namespace, "Updating", "Updating VyOS router configuration")
        
        # Extract updated router configuration
        router_config = {
            'name': name,
            'hostname': spec['hostname'],
            'router_id': spec['router_id'],
            'image': spec.get('image', 'vyos:1.5'),
            'interfaces': spec.get('interfaces', []),
            'vrfs': spec.get('vrfs', []),
            'protocols': spec.get('protocols', {}),
            'services': spec.get('services', {}),
            'qos': spec.get('qos', {}),
            'firewall': spec.get('firewall', {})
        }
        
        # Update the VyOS router using Ansible
        result = await update_vyos_router(router_config)
        
        if result['success']:
            await update_status(name, namespace, "Running", "VyOS router updated successfully")
            logger.info(f"Successfully updated VyOSRouter {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to update router: {result['error']}")
            raise kopf.PermanentError(f"VyOS router update failed: {result['error']}")
            
    except Exception as e:
        logger.error(f"Failed to update VyOSRouter {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.delete('guardian.dev', 'v1', 'vyosrouter')
async def delete_vyosrouter(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSRouter deletion using Ansible"""
    logger.info(f"Deleting VyOSRouter: {name} in namespace: {namespace}")
    
    try:
        # Extract interfaces from spec for proper veth cleanup
        interfaces = spec.get('interfaces', [])
        
        # Delete the VyOS router container using Ansible
        result = await delete_vyos_router(name, interfaces)
        
        if result['success']:
            logger.info(f"Successfully deleted VyOSRouter {name}")
        else:
            logger.warning(f"Failed to delete VyOS router {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during VyOSRouter deletion {name}: {e}")
        # Don't raise error on delete failure

#########################################################################
# Status Management
#########################################################################
async def update_status(name: str, namespace: str, phase: str, message: str, 
                       container_id: Optional[str] = None, ip_address: Optional[str] = None,
                       interfaces: Optional[list] = None):
    """Update the status of a VyOSRouter resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='guardian.dev/v1', kind='VyOSRouter')
    
    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()
    
    if 'status' not in resource_dict:
        resource_dict['status'] = {}
    
    status = {
        'phase': phase,
        'message': message
    }
    
    if container_id:
        status['container_id'] = container_id
    
    if ip_address:
        status['ip_address'] = ip_address
    
    if interfaces:
        status['interfaces'] = interfaces
    
    resource_dict['status'].update(status)

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
            logger.warning(f"Status subresource not enabled for VyOSRouter {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for VyOSRouter {name}: {e}")
