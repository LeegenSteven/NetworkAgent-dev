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

# Agent description
description="""
A network engineer agent. The network engineer agent's job is to communicate with the user to help them create and delete 
network services and/or network locations. The engineer agent takes and objective and creates a plan of changes needed to deliver it.

The network engineer agent can help the user with network create and delete tasks such as:
- create and delete network locations
- create, delete, and reinstall network services
"""

# Chat Skill description
chat_description="""
A network engineer agent. The network engineer agent's job is to communicate with the user to help them create and delete 
network services and/or network locations. The engineer agent takes and objective andcreates a plan of changes needed to deliver it.

The network engineer agent can help the user with network create and delete tasks such as:
- create and delete network locations
- create, delete and reinstall network services
"""
chat_tags = ['chat']
chat_examples = [
"""
Create a plan for a network location called brian with cidr 10.0.50.0/24
""",
"""
Create a plan to create a fully working 5g network service
""",
"""
Create a plan to reinstall a failed wireguard network service named cellsite1-vpn1234
""",
]

# Chat Skill description
background_description="""
A background network engineer agent that can received requests from another Agent. The network engineer 
agent's job is to communicate with the user to help them create and delete network services and/or network 
locations. The engineer agents takes and objective and creates a plan of changes needed to deliver it.

The network engineer agent can help other agents with network create and delete tasks such as:
- create and delete network locations
- create, delete and reinstall network services
"""
background_tags = ['background']
background_examples = [
"""
{
"objective": "Create a plan for a network location called brian with cidr 10.0.50.0/24"
}
""",
"""
{
"objective": "Create a plan to create a fully working 5g network service"
}
""",
"""
Create a plan to reinstall a failed wireguard network service named cellsite1-vpn1234
""",
]
