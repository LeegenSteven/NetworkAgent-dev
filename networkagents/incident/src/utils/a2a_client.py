#!/usr/bin/env python3
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
import httpx
import logging
import argparse
import sys
from typing import Any
from uuid import uuid4
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendMessageRequest,
    SendMessageSuccessResponse,
    GetTaskRequest,
    GetTaskSuccessResponse,
    TaskState,
    Task,
    MessageSendParams,
    TaskQueryParams,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
)
logger = logging.getLogger(__name__)

class EngineerAgentClient:
    """A client to connect to and test the Engineer Agent."""

    def __init__(self, address: str):
        """
        Initialize the Engineer Agent client.
        
        Args:
            address: The address of the Engineer Agent server.
        """
        self.address = address
        self.agent_card = None
        self.agent_client = None

    async def create_client(self):
        """Create an A2A client to connect to the Engineer Agent."""
        logger.info(f"Creating client for Engineer Agent with address {self.address}")

        async with httpx.AsyncClient(timeout=60.0) as httpx_client:
            card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=self.address)
            self.agent_card = await card_resolver.get_agent_card()
            self.agent_card.url = self.address
            logger.info(f"Connected to agent: {self.agent_card.name}")

        self.agent_client = await A2AClient.get_client_from_agent_card_url(httpx_client=httpx.AsyncClient(timeout=60.0), base_url=self.address)
        # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
        self.agent_client.url=self.address

        return self.agent_client

    def create_send_message_payload(self, text: str, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", text, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'type': 'data', 'data': {'objective': text }}],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload

    async def get_task_status(self, task_id: str):
        """
        Get the current status of a task.
        
        Args:
            task_id: The ID of the task to get the status for.
            
        Returns:
            The current task status state or None if an error occurred.
        """
        if not self.agent_client:
            await self.create_client()
            
        logger.info(f"Getting task status for task ID: {task_id}")
        
        try:
            # Create the request to get task status
            params = TaskQueryParams(id=task_id)
            request = GetTaskRequest(params=params)
            
            # Send the request
            response = await self.agent_client.get_task(request)
            
            if isinstance(response.root, GetTaskSuccessResponse):
                task = response.root.result
                logger.info(f"Task state is {task.status.state}")
                return task.status.state
            else:
                logger.error(f"Unexpected response: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting task status: {str(e)}", exc_info=True)
            return None

    async def send_task(self, task_text: str):
        """
        Send a task to the Engineer Agent.
        
        Args:
            task_text: The text of the task to send.
            
        Returns:
            The final task status or None if an error occurred.
        """
        if not self.agent_client:
            await self.create_client()
            
        logger.info(f"Sending task to Engineer Agent: {task_text}")
        
        # Create a message with data part
        send_payload = self.create_send_message_payload(text=task_text)
        params = MessageSendParams(**send_payload)
                
        logger.info(f"Request parameters: {params}")

        try:
            logger.info("Sending non-streaming request with data part...")
            request = SendMessageRequest(params=params)
            response = await self.agent_client.send_message(request)
            logger.info(response)
            
            if isinstance(response.root, SendMessageSuccessResponse):
                if isinstance(response.root.result, Task):
                    task = response.root.result
                    task_id = task.id
                    logger.info(f"Task created with ID: {task_id}")  
                    return task_id
                else:
                    logger.error("Response did not contain a Task object")
                    return None
            else:
                logger.error(f"Unexpected response: {response}")
                return None
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Engineer Agent: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error communicating with Engineer Agent: {str(e)}", exc_info=True)
            return None
