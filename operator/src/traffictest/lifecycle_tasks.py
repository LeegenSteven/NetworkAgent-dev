import asyncio
import ansible_runner
import os
import utils.constants as constants
import logging
from typing import Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based TrafficTest Management
#########################################################################

async def create_traffic_test(networkvm_ip_address:str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a TrafficTest using Ansible"""
    logger.info(f"Creating TrafficTest: {spec.get('source_device')} -> {spec.get('destination_device')}")

    # Extract required fields from spec
    source_device = spec.get('source_device')
    source_ip = spec.get('source_ip')
    destination_device = spec.get('destination_device')
    destination_ip = spec.get('destination_ip')
    protocol = spec.get('protocol', 'TCP')
    port = spec.get('port', 5201)
    duration = spec.get('duration', 60)
    bandwidth = spec.get('bandwidth', '10Mbps')
    
    # Traffic pattern configuration
    pattern_type = spec.get('pattern_type', 'constant')
    pattern_config = spec.get('pattern_config', {})
    concurrent_users = spec.get('concurrent_users', 1)
    session_duration = spec.get('session_duration')
    think_time = spec.get('think_time', 0)
    
    # Metrics configuration
    metrics_enabled = spec.get('metrics_enabled', True)
    metrics_interval = spec.get('metrics_interval', 5)

    # Prepare extra variables for Ansible playbook
    extravars = {
        'operation': 'create',
        'test_name': spec.get('test_name'),
        'source_device': source_device,
        'source_ip': source_ip,
        'destination_device': destination_device,
        'destination_ip': destination_ip,
        'protocol': protocol,
        'port': port,
        'duration': duration,
        'bandwidth': bandwidth,
        'pattern_type': pattern_type,
        'pattern_config': pattern_config,
        'concurrent_users': concurrent_users,
        'session_duration': session_duration,
        'think_time': think_time,
        'metrics_enabled': metrics_enabled,
        'metrics_interval': metrics_interval,
        'start_time': datetime.now(timezone.utc).isoformat(),
    }

    result = await _run_ansible_playbook(networkvm_ip_address,'traffic.yaml', extravars)
    logger.info(f"TrafficTest creation result: {result}")

    if result['success']:
        return {
            'success': True,
            'start_time': extravars['start_time'],
            'message': 'Traffic test started successfully'
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during TrafficTest creation')
        }

async def delete_traffic_test(networkvm_ip_address:str,spec: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a TrafficTest using Ansible"""
    logger.info(f"Deleting TrafficTest: {spec.get('source_device')} -> {spec.get('destination_device')}")
    
    extravars = {
        'operation': 'delete',
        'source_device': spec.get('source_device'),
        'destination_device': spec.get('destination_device'),
        'protocol': spec.get('protocol', 'TCP'),
        'port': spec.get('port', 5201),
        'end_time': datetime.now(timezone.utc).isoformat()
    }

    result = await _run_ansible_playbook(networkvm_ip_address,'traffic.yaml', extravars)

    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None,
        'end_time': extravars['end_time']
    }

async def get_traffic_test_status(networkvm_ip_address:str,spec: Dict[str, Any]) -> Dict[str, Any]:
    """Get current status of a TrafficTest using Ansible"""
    logger.info(f"Getting TrafficTest status: {spec.get('source_device')} -> {spec.get('destination_device')}")
    
    extravars = {
        'operation': 'status',
        'source_device': spec.get('source_device'),
        'destination_device': spec.get('destination_device'),
        'protocol': spec.get('protocol', 'TCP'),
        'port': spec.get('port', 5201)
    }

    result = await _run_ansible_playbook(networkvm_ip_address,'traffic.yaml', extravars)

    if result['success']:
        return {
            'success': True,
            'status': result.get('status', 'Unknown'),
            'current_metrics': result.get('current_metrics', {}),
            'message': result.get('message', 'Status retrieved successfully')
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Failed to get traffic test status')
        }

#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(networkvm_ip_address:str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables"""
    
    # Get the Ansible semaphore for throttling
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()
    
    # Prepare host inventory
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': networkvm_ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    
    logger.info(f"Running Ansible playbook: {playbook}")
    logger.info(f"Extra vars: {extravars}")
    
    def run_ansible():
        """Wrapper function to run ansible_runner.run_async"""
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=constants.basedir + "/traffictest/playbooks",
                inventory={'all': hosts},
                playbook=playbook,
                extravars=extravars,
                quiet=False,
                verbosity=1
            )
            # Wait for the thread to complete
            thread.join()
            return runner
        except Exception as e:
            logger.error(f"Ansible execution failed: {e}")
            return None
    
    # Throttle concurrent Ansible executions using semaphore
    async with semaphore:
        logger.info(f"Acquired Ansible semaphore for playbook: {playbook}")
        # Execute in thread pool to avoid blocking the async event loop
        loop = asyncio.get_event_loop()
        runner = await loop.run_in_executor(None, run_ansible)
        
        if runner is None:
            return {
                'success': False,
                'error': 'Failed to execute Ansible playbook'
            }
        
        if runner.status == 'successful':
            # Extract results from Ansible facts if available
            result_data = {}
            
            # Try to get results from the last event
            for event in runner.events:
                if event.get('event') == 'runner_on_ok':
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})
                    
                    # Extract traffic test information from Ansible results
                    if 'traffic_test_id' in res:
                        result_data['traffic_test_id'] = res['traffic_test_id']
                    if 'start_time' in res:
                        result_data['start_time'] = res['start_time']
                    if 'end_time' in res:
                        result_data['end_time'] = res['end_time']
                    if 'status' in res:
                        result_data['status'] = res['status']
                    if 'current_metrics' in res:
                        result_data['current_metrics'] = res['current_metrics']
                    if 'message' in res:
                        result_data['message'] = res['message']
                    if 'results_file' in res:
                        result_data['results_file'] = res['results_file']
            
            return {
                'success': True,
                **result_data
            }
        else:
            # Extract error information
            error_msg = f"Ansible playbook failed with status: {runner.status}"
            
            # Try to get more detailed error from events
            for event in runner.events:
                if event.get('event') == 'runner_on_failed':
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})
                    if 'msg' in res:
                        error_msg = res['msg']
                    elif 'stderr' in res:
                        error_msg = res['stderr']
                    break
        
        logger.error(f"Ansible playbook execution failed: {error_msg}")
        return {
            'success': False,
            'error': error_msg
        }
