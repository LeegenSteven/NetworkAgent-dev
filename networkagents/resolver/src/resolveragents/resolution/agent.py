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

from google.adk.agents import LlmAgent
from resolveragents.utils.db import update_database
from resolveragents.utils.notification import notify_supervisor
import logging
import httpx
import logging
from typing import Any
from uuid import uuid4
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendMessageRequest,
    MessageSendParams,
)
import os
from .agent_prompt import resolution_prompt

# https://github.com/google/adk-python/tree/main/contributing/samples/human_in_loop

logger = logging.getLogger(__name__)

async def create_client():
    """Create an A2A client to connect to the Engineer Agent."""
    logger.info(f"Creating client for Engineer Agent")

    engineer_url = os.getenv("ENGINEER_URL", "http://127.0.0.1:8081")

    async with httpx.AsyncClient(timeout=60.0) as httpx_client:
        card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=engineer_url)
        agent_card = await card_resolver.get_agent_card()
        agent_card.url = engineer_url
        logger.info(f"Connected to agent: {agent_card.name}")

    agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=60.0), agent_card=agent_card)
    agent_client.url=engineer_url
    return agent_client

def create_send_message_payload(text: str) -> dict[str, Any]:
    """Helper function to create the payload for sending a task."""

    logger.info("create request with %s", text)

    payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [{'kind': 'data', 'data': {'objective': text ,'preapproved': False}}],
            'messageId': uuid4().hex,
        },
    }

    return payload


###################################################
# Engineer agent call
###################################################
async def make_network_change(change_request: str)-> bool:
    """
    Request the network engineer agent to make a network change to resolve the reported incident.

    The network engineer agent's job is to communicate with the user to help them create and delete 
    network services and/or network locations. The engineer agent takes and objective andcreates a plan of changes needed to deliver it.

    The network engineer agent can help the user with network create and delete tasks such as:
    - create and delete network locations
    - create, delete and reinstall network services
    
    Examples Network change requests:
    * Create a plan for a network location called brian with cidr 10.0.50.0/24"
    * Create a plan to create a fully working 5g network service"
    * Create a plan to reinstall a failed wireguard network service named cellsite1-vpn1234

    Args:
        change_request (str): a network change that can be implemented by the engineer agent        
    Returns:
        success (bool)
    """
    logger.info(f"make network change {change_request}")
    
    agent_client = await create_client()
    send_payload = create_send_message_payload(text=change_request)
    params = MessageSendParams(**send_payload)
    request = SendMessageRequest(id = str(uuid4()),params=params)
    response = await agent_client.send_message(request)
    logger.info(response)


###################################################
# Resolve Agent
###################################################
resolution_agent = LlmAgent(
    name="ResolutionAgent",
    model="gemini-2.5-flash",
    instruction=resolution_prompt,
    description="Find and execute a resolution to the identified root cause.",
    tools= [make_network_change],
    after_agent_callback=[notify_supervisor,update_database],
    output_key='resolution'
)