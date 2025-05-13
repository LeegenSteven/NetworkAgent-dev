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
import ansible_runner
import utils.constants as constants
from utils.compute import *

logger = logging.getLogger(__name__)

@kopf.on.create('connectivitytest')
async def createtest(spec, name, namespace, logger, **kwargs):
    # build the a and b end variables
    client=spec.get('virtualmachines')[0]
    server=spec.get('virtualmachines')[1]

    logger.info("Create test case between %s and %s", client, server)

    client_mgmt_ip=await get_ip(namespace, client)
    server_mgmt_ip=await get_ip(namespace, server)

    if client_mgmt_ip is None or server_mgmt_ip is None:
        raise kopf.PermanentError("waiting for IP addresses")
    client_data_ip=await get_ip(namespace, client, 'dataplane')
    server_data_ip=await get_ip(namespace, server, 'dataplane')
    hosts = {
        'hosts': {
            'client': {
                'ansible_host': client_mgmt_ip,
                'data_ip': client_data_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            },
            'server': {
                'ansible_host': server_mgmt_ip,
                'data_ip': server_data_ip,
                'ansible_user': 'admin_briannaughton_altostrat_co',
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(json.dumps(hosts, indent=4))
    r = ansible_runner.run(private_data_dir=constants.basedir+"/vpn/tests/playbooks", 
                           inventory={'all': hosts},
                           playbook='run.yaml')

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)



@kopf.on.delete('connectivitytest')
async def deletetest(spec, name, namespace, logger, **kwargs):
    logger.info("Delete test case")
    
    client=spec.get('virtualmachines')[0]
    server=spec.get('virtualmachines')[1]

    client_mgmt_ip=await get_ip(namespace, client)
    server_mgmt_ip=await get_ip(namespace, server)

    if client_mgmt_ip is None or server_mgmt_ip is None:
        raise kopf.PermanentError("can't find ip addresses for client or server")

    hosts = {
        'hosts': {
            'client': {
                'ansible_host': client_mgmt_ip,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            },
            'server': {
                'ansible_host': server_mgmt_ip,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    logger.info(json.dumps(hosts, indent=4))
    r = ansible_runner.run(private_data_dir=constants.basedir+"/vpn/tests/playbooks", 
                           inventory={'all': hosts},
                           playbook='stop.yaml')

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
