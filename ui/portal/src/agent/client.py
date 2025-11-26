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
import httpx
from typing import Any
from uuid import uuid4
from a2a.types import (
    SendMessageRequest,
    MessageSendParams,
)
from a2a.client import A2ACardResolver, A2AClient

logger = logging.getLogger(__name__)


async def create_client():
    """Create an A2A client to connect to the Order Agent."""
    logger.info(f"Creating client for Order Agent")

    engineer_url = os.getenv("ORDERAGENT_URL", "http://127.0.0.1:8089")

    async with httpx.AsyncClient(timeout=60.0) as httpx_client:
        card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=engineer_url)
        agent_card = await card_resolver.get_agent_card()
        agent_card.url = engineer_url
        logger.info(f"Connected to agent: {agent_card.name}")

    agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=60.0), agent_card=agent_card)
    agent_client.url=engineer_url
    return agent_client

def create_send_message_payload(data: dict) -> dict[str, Any]:
    """Helper function to create the payload for sending a task."""

    logger.info("create request with %s", dict)

    payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [{'kind': 'data', 'data': data}],
            'messageId': uuid4().hex,
        },
    }

    return payload


###################################################
# Engineer agent call
###################################################
async def send_order(order: dict)-> bool:
    """
    Request the Order agent to execute a request from the user.

    Args:
        order (str): a customer order
    Returns:
        success (bool)
    """
    logger.info(f"Send order {order}")
    
    agent_client = await create_client()
    send_payload = create_send_message_payload(data=order)
    params = MessageSendParams(**send_payload)
    request = SendMessageRequest(id = str(uuid4()),params=params)
    response = await agent_client.send_message(request)
    logger.info(response)