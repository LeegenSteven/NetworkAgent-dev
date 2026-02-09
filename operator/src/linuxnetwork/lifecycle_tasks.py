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
import asyncio
import ansible_runner
import os
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based Linux Network Management
#########################################################################

async def create_linux_network(ip_address:str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Linux network using Ansible"""
    logger.info(f"Creating Linux network: {spec['name']}")

    # Prepare extra variables for Ansible playbook
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
        'bandwidth': spec.get('bandwidth'),
        'gateway_ip': spec.get('gateway'),
        'operation': 'create'
    }

    result = await _run_ansible_playbook(ip_address, 'create_network.yaml', extravars)
    logger.info(f"Linux network creation result: {result}")

    if result['success']:
        # Capture the default interface
        default_interface = result.get('default_interface', 'unknown')
        interface_ip = result.get('interface_ip', 'unknown')
        default_gateway = result.get('default_gateway', 'unknown')
        return {
            'success': True,
            'default_interface': default_interface,
            'interface_ip': interface_ip,
            'default_gateway': default_gateway
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during network creation')
        }

async def delete_linux_network(ip_address:str, spec: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Linux network using Ansible"""
    logger.info(f"Deleting Linux network: {spec.get('name')}")
    
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
    }

    # If network_type is management, pass the interface from status
    if spec.get('network_type') == 'management' and status:
        extravars['parent_interface'] = status.get('interface')
        extravars['interface_ip'] = status.get('interface_ip')
        extravars['default_gateway'] = status.get('gateway')

    result = await _run_ansible_playbook(ip_address, 'delete_network.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }

async def get_network_status(ip_address:str, network_name: str) -> Dict[str, Any]:
    """Get Docker network status using Ansible"""
    logger.info(f"Checking status of Docker network: {network_name}")
    
    extravars = {
        'network_name': network_name
    }
    
    result = await _run_ansible_playbook(ip_address, 'status_network.yaml', extravars)
    
    return {
        'exists': result.get('exists', False),
        'network_info': result.get('network_info', {}),
        'error': result.get('error') if not result['success'] else None
    }

#########################################################################
# Ansible Execution Helper
#########################################################################
    
async def _run_ansible_playbook(ip_address:str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables"""
    
    # Get the Ansible semaphore for throttling
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()
    
    # Prepare host inventory
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': ip_address,
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
                private_data_dir=constants.basedir + "/linuxnetwork/playbooks",
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
            
            # Try to get results from all events
            for event in runner.events:
                if event.get('event') == 'runner_on_ok':
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})
                    
                    # Extract ansible_facts if they exist
                    if 'ansible_facts' in res:
                        ansible_facts = res['ansible_facts']
                        
                        # Extract default_interface if present
                        if 'default_interface' in ansible_facts:
                            result_data['default_interface'] = ansible_facts['default_interface']
                            logger.info(f"Captured default_interface: {ansible_facts['default_interface']}")
                        
                        # Extract default_gateway if present
                        if 'default_gateway' in ansible_facts:
                            result_data['default_gateway'] = ansible_facts['default_gateway']
                            logger.info(f"Captured default_gateway: {ansible_facts['default_gateway']}")

                        # Extract interface_ip if present
                        if 'interface_ip' in ansible_facts:
                            result_data['interface_ip'] = ansible_facts['interface_ip']
                            logger.info(f"Captured interface_ip: {ansible_facts['interface_ip']}")

            logger.info(f"Final extracted data: {result_data}")
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
