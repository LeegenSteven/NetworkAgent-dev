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
import kopf
from utils.compute import *
from free5gc.build.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a new free5gc builder
##########################################
@kopf.on.create('google.dev', 'v1', 'free5gc')
async def free5gcbuild(spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create free5gc build {name} with spec: {spec}")

  # create UERANSIM VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name, # vm name
                        None, # external IP
                        None, # set this to the target network name to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        release="ubuntu-2004-lts",
                        monitor=True, # set to false so this VM is not scraped by prometheus
                        graph=True)

  # install free5gc builder to VM 
  await run_install(namespace, name)

  return {
      "status":"Complete", 
  }

