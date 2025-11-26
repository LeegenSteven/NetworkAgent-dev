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

validate_design_prompt="""
The following changes were proposed to the network. The network changes describe a set of steps to be sent to the network
engineer agent. Your job is to ensure the network changes provide enough information for the engineer to turn into actions on the 
network. The steps may need to be further broken down or expanded to provide enough information for the engineer to turn into actions on the 
network. 

The network changes are below:
---
{network_changes}
---

The network engineer uses the 'getNetworkDesign' tool to understand how to break down high level network change requests
into individual network change steps. The network engineer also uses 'getServiceDefinitions' tool to 
understand the information needed to provision individual network services. 

Please validate the network change information provided can be decomposed into steps that can be executed by the network engineer agent.
"""