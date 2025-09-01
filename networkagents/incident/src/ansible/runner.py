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
import logging
import os
import utils.constants as constants
import asyncio
import ansible_runner
from utils.k8s import get_ip_address

logger = logging.getLogger(__name__)

def event_handler(data):
    if "stdout" in data:
        logger.info(data['stdout'])
    elif "stderr" in data:
        logger.info(data['stderr'])
    else:
        logger.info(data)

async def run_incident(parent_node, parent_kind, child_node, child_kind, incident_type):
    logger.info(f"Running incident playbook for {incident_type}")
    logger.info(f"Parent node: {parent_node} (kind: {parent_kind})")
    logger.info(f"Child node: {child_node} (kind: {child_kind})")
    
    # Determine the playbook directory and file based on child kind and incident type
    playbook_dir, playbook_file = get_playbook_path(parent_kind, incident_type)

    # get the VM ip address from kubernetes
    if isinstance(child_node, dict):
        child_node_name = child_node.get('name')
        if not child_node_name:
            raise ValueError(f"Child node dictionary missing 'name' field: {child_node}")
    else:
        child_node_name = child_node
    
    if not child_node_name:
        raise ValueError(f"Invalid child node name: {child_node_name}")
    
    ip_address = await get_ip_address(child_node_name)
    
    # run ansible playbook based on the incident type and node kind
    extravars = {
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir,
    }
    hosts = {
        'hosts': {
            "target": {
                'ansible_host': ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }

    logger.info(f"Running playbook: {playbook_dir}/{playbook_file}")
    logger.info(f"Hosts: {hosts}")
    logger.info(f"Extra vars: {extravars}")

    def run_ansible():
        """Wrapper function to run ansible_runner.run_async"""
        thread, runner = ansible_runner.run_async(
            private_data_dir=playbook_dir, 
            inventory={'all': hosts},
            playbook=playbook_file,
            event_handler=event_handler,
            extravars=extravars
        )
        # Wait for the thread to complete
        thread.join()
        return runner

    # Execute in thread pool to avoid blocking the async event loop
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, run_ansible)

    logger.info("status = %s", r.status)
    return r.status

def get_playbook_path(parentkind,incident_type):
    """
    Determine the appropriate playbook directory and file based on node kind and incident type.
    This function selects the right playbook to run based on the node's characteristics.
    """
    logger.info(f"Selecting playbook for kind: {parentkind}, incident_type: {incident_type}")
    
    # Default playbook directory and file
    base_dir = constants.basedir

    # Map incident types to playbook files
    incident_directories = {
        'kill-process': 'process',
        'throttle-interface': 'throttle'
    }    
    # Map node kinds to specific directories if needed
    kind_directories = {
        'UERanSIM': 'ueransim',
        'WireguardAppliance': 'wireguard'
    }  

    if parentkind in kind_directories and incident_type in incident_directories:
        playbook_dir = os.path.join(base_dir, 'ansible', kind_directories[parentkind], incident_directories[incident_type], "playbooks")
    else:
        raise Exception
    
    logger.info(f"Selected playbook: {playbook_dir}/run.yaml")
    
    return playbook_dir, "run.yaml"
