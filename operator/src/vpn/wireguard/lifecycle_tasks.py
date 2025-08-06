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
import kubernetes
import kopf
import ansible_runner
import utils.constants as constants
import asyncio
import os
from utils.ansible import event_handler

logger = logging.getLogger(__name__)


#####################################################################
# Install VPN software on VM
#####################################################################
async def install_vpn(servicename, vmname, mgmt_ip_address, data_ip_address, tunnel_address, tunnel_cidr, keys, peers):
    logger.debug(f"Install VPN on VM {vmname}")

    extravars = {
        'servicename': servicename,
        'data_ip_address': data_ip_address,
        'tunnel_address': tunnel_address,
        'tunnel_cidr': tunnel_cidr,
        'default_interface': 'ens6' ,
        'peers' : peers,
        'keys': keys, 
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir
    }
    hosts = {
        'hosts': {
            vmname: {
                'ansible_host': mgmt_ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(hosts)
    logger.info(extravars)

    def run_ansible():
        """Wrapper function to run ansible_runner.run_async"""
        thread, runner = ansible_runner.run_async(
            private_data_dir=constants.basedir+"/vpn/wireguard/playbooks", 
            inventory={'all': hosts},
            playbook='install.yaml',
            event_handler=event_handler,
            extravars=extravars
        )
        # Wait for the thread to complete
        thread.join()
        return runner

    # Execute in thread pool to avoid blocking the async event loop
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, run_ansible)

    if r.status != 'successful':
        logger.info(f"Waiting for Edge VPN VM {vmname} used by service {servicename} to come up.")
        raise kopf.TemporaryError("Waiting for VM to come up.", delay=10)
