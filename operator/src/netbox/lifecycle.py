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
from netbox.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','netbox')
async def create_netbox(spec, name, namespace, logger, **kwargs):
    logger.debug("Create netbox instance")

    # Create external IP address
    await create_external_ip(namespace, "netbox", os.getenv("GOOGLE_REGION"))
    external_ip_address = await get_ip_address(namespace, "netbox")

    # Create VM and attach IP address
    await create_compute(namespace, 
                         name,
                         "netbox",
                         external_ip_address, # replace with None when only private IP address
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         monitor=False) # set to false so this VM is not scraped by prometheus

    # Install Gitea
    await run_netbox_install(namespace, external_ip_address)

    return {"status": "Running", "external_ip_address": external_ip_address}

