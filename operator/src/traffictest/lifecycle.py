import kopf
import logging
import kubernetes
import asyncio
from datetime import datetime, timezone
from traffictest.lifecycle_tasks import (
    create_traffic_test,
    delete_traffic_test,
    get_traffic_test_status,
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# TrafficTest Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'traffictest')
async def create_traffic_test_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle TrafficTest creation using Ansible"""
    logger.info(f"Creating TrafficTest: {name} in namespace: {namespace}")
    logger.info(f"spec: {spec}")
    
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        # Validate required Devices exist and are ready
        source_device = spec.get('source_device')
        destination_device = spec.get('destination_device')
        
        if not source_device or not destination_device:
            error_msg = "Both source_device and destination_device are required"
            logger.error(f"TrafficTest {name}: {error_msg}")
            await update_status(name, namespace, "Failed", error_msg)
            raise kopf.PermanentError(error_msg)
        
        # Check if the required Devices are ready
        source_ready, source_ip = await check_device_ready(source_device, namespace)
        dest_ready, dest_ip = await check_device_ready(destination_device, namespace)

        if not source_ready or not dest_ready:
            error_msg = f"Waiting for Devices to be ready (source: {source_ready}, dest: {dest_ready})"
            logger.warning(f"TrafficTest {name}: {error_msg}")
            await update_status(name, namespace, "Pending", error_msg)
            
            # Raise temporary error to retry later
            raise kopf.TemporaryError(error_msg, delay=30)

        # Update status to indicate creation has started
        await update_status(name, namespace, "Deploying", "Starting traffic test deployment")

        # Add test name and IPs to spec for tracking
        spec_with_name = dict(spec)
        spec_with_name['test_name'] = name
        spec_with_name['source_ip'] = source_ip
        spec_with_name['destination_ip'] = dest_ip

        # Create the TrafficTest using Ansible
        result = await create_traffic_test(ip_address, spec_with_name)

        if result['success']:
            # Update status to running
            await update_status(
                name, namespace, "Running", 
                f"Traffic test started successfully at {result.get('start_time', 'unknown time')}",
                additional_data={
                    'start_time': result.get('start_time'),
                }
            )
            logger.info(f"Successfully started TrafficTest {name}")
            
            # Start background task to monitor the test
            # asyncio.create_task(monitor_traffic_test(name, namespace, spec_with_name, ip_address))
            
        else:
            await update_status(name, namespace, "Failed", f"Failed to start traffic test: {result['error']}")
            raise kopf.PermanentError(f"TrafficTest creation failed: {result['error']}")

    except kopf.TemporaryError:
        # Re-raise temporary errors for retry
        raise
    except kopf.PermanentError:
        # Re-raise permanent errors
        raise
    except Exception as e:
        logger.error(f"Failed to create TrafficTest {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise kopf.PermanentError(str(e))

@kopf.on.delete('google.dev', 'v1', 'traffictest')
async def delete_traffic_test_handler(body, spec, name, namespace, logger, **kwargs):
    """Handle TrafficTest deletion using Ansible"""
    logger.info(f"Deleting TrafficTest: {name} in namespace: {namespace}")

    try:
        # Get Network VM IP address
        ip_address = await get_ip("automation", "networkvm")
        if ip_address is None:
            logger.warning("No IP address found on Network VM, skipping traffic test deletion")
            return
        
        # Add test name to spec for tracking
        spec_with_name = dict(spec)
        spec_with_name['test_name'] = name
        
        # Delete the traffic test using Ansible
        result = await delete_traffic_test(ip_address, spec_with_name)
        
        if result['success']:
            logger.info(f"Successfully deleted TrafficTest {name}")
        else:
            logger.warning(f"Failed to delete TrafficTest {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during TrafficTest deletion {name}: {e}")
        # Don't raise error on delete failure

#########################################################################
# Background Monitoring
#########################################################################

async def monitor_traffic_test(name: str, namespace: str, spec: dict, ip_address: str):
    """Monitor a running traffic test and update status"""
    logger.info(f"Starting monitoring for TrafficTest {name}")
    
    duration = spec.get('duration', 60)
    metrics_interval = spec.get('metrics_interval', 5)
    
    # Monitor for the duration of the test
    start_time = datetime.now(timezone.utc)
    
    try:
        while True:
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            if elapsed >= duration:
                # Test should be completed
                logger.info(f"TrafficTest {name} duration reached, checking final status")
                
                # Get final status
                status_result = await get_traffic_test_status(ip_address, spec)
                
                if status_result['success']:
                    await update_status(
                        name, namespace, "Completed",
                        f"Traffic test completed successfully after {duration}s",
                        additional_data={
                            'end_time': datetime.now(timezone.utc).isoformat(),
                            'current_metrics': status_result.get('current_metrics', {})
                        }
                    )
                else:
                    await update_status(
                        name, namespace, "Failed",
                        f"Traffic test failed: {status_result.get('error', 'Unknown error')}"
                    )
                break
            
            # Get current status and metrics
            try:
                status_result = await get_traffic_test_status(ip_address, spec)
                
                if status_result['success']:
                    current_metrics = status_result.get('current_metrics', {})
                    
                    await update_status(
                        name, namespace, "Running",
                        f"Traffic test running ({elapsed:.0f}s/{duration}s)",
                        additional_data={'current_metrics': current_metrics}
                    )
                else:
                    logger.warning(f"Failed to get status for TrafficTest {name}: {status_result.get('error')}")
                    
            except Exception as e:
                logger.error(f"Error monitoring TrafficTest {name}: {e}")
            
            # Wait for next check
            await asyncio.sleep(min(metrics_interval, 30))  # Check at least every 30s
            
    except Exception as e:
        logger.error(f"Error in TrafficTest monitoring {name}: {e}")
        await update_status(name, namespace, "Failed", f"Monitoring failed: {str(e)}")

#########################################################################
# Device Validation
#########################################################################

async def check_device_ready(device_name: str, namespace: str) -> bool:
    """
    Check if the specified Device exists and has status 'Ready'.
    
    Args:
        device_name: Name of the Device resource
        namespace: Kubernetes namespace
    
    Returns:
        bool: True if Device is ready, False otherwise
        device_ip: IP address of the Device
    """
    if not device_name:
        logger.warning("No device_name provided for Device check")
        return False, None

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')
    
    try:
        device = api.get(name=device_name, namespace=namespace)
        device_dict = device.to_dict()
        
        status = device_dict.get('status', {})
        phase = status.get('phase', 'Unknown')
        
        if phase != 'Ready':
            logger.warning(f"Device '{device_name}' is in phase '{phase}', not Ready")
            return False
        else:
            logger.info(f"Device '{device_name}' is Ready")
            return True, device_dict.get('spec', {}).get('ip_address', None)

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning(f"Device '{device_name}' not found")
            return False, None
        else:
            logger.error(f"Error checking Device '{device_name}': {e}")
            return False, None
    except Exception as e:
        logger.error(f"Unexpected error checking Device '{device_name}': {e}")
        return False, None

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, additional_data: dict = None):
    """Update the status of a TrafficTest resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='TrafficTest')

    try:
        resource = api.get(name=name, namespace=namespace)
        resource_dict = resource.to_dict()

        if 'status' not in resource_dict:
            resource_dict['status'] = {}

        status = {
            'phase': phase,
            'message': message
        }
        
        # Add timestamps
        if phase == "Running" and 'start_time' not in resource_dict['status']:
            status['start_time'] = datetime.now(timezone.utc).isoformat()
        elif phase in ["Completed", "Failed", "Stopped"]:
            status['end_time'] = datetime.now(timezone.utc).isoformat()
        
        # Add additional data if provided
        if additional_data:
            status.update(additional_data)
            
        resource_dict['status'].update(status)

        api.patch(
            namespace=namespace,
            name=name,
            body=resource_dict,
            content_type='application/merge-patch+json',
            subresource='status'
        )
        
        logger.debug(f"Updated status for TrafficTest {name}: {phase} - {message}")
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for TrafficTest {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for TrafficTest {name}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status for TrafficTest {name}: {e}")
