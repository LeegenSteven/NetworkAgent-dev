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

class OrderAgentClient:
    """A client to connect to and test the Order Agent."""

    def __init__(self, address: str):
        """
        Initialize the Order Agent client.
        
        Args:
            address: The address of the Order Agent server.
        """
        self.address = address
        self.agent_card = None
        self.agent_client = None

    async def create_client(self):
        """Create an A2A client to connect to the Order Agent."""
        logger.info(f"Creating client for Order Agent with address {self.address}")

        async with httpx.AsyncClient(timeout=60.0) as httpx_client:
            card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=self.address)
            self.agent_card = await card_resolver.get_agent_card()
            self.agent_card.url = self.address
            logger.info(f"Connected to agent: {self.agent_card.name}")

        self.agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=60.0), agent_card=self.agent_card)
        # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
        self.agent_client.url=self.address

        return self.agent_client

    def create_send_message_payload(self, data: dict, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", data, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'data', 'data': data }],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload

    async def send_task(self):
        """
        Send a task to the Order Agent.
        
        Args:
            poll_interval: Time in seconds between polling attempts (default: 5)
            
        Returns:
            The final task status or None if an error occurred.
        """
        if not self.agent_client:
            await self.create_client()
            
        logger.info(f"Sending task to Order Agent")
        
        order = {
            'sliceType': 'embb',
            'bandwidth': '100mbps',
            'geographicArea': ['london', 'ny'],
            'duration': '100days',
        }
        
        # Create a message with data part
        send_payload = self.create_send_message_payload(data=order)
        params = MessageSendParams(**send_payload)
                
        logger.info(f"Request parameters: {params}")

        try:
            logger.info("Sending non-streaming request with data part...")
            request = SendMessageRequest(id=uuid4().hex, params=params)
            response = await self.agent_client.send_message(request)
            logger.info(response)
            
            if isinstance(response.root, SendMessageSuccessResponse):
                if isinstance(response.root.result, Task):
                    task = response.root.result
                    return task.status
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

async def main():
    """Main function to run the test."""
    parser = argparse.ArgumentParser(description="Test the Order Agent using A2A client")
    parser.add_argument(
        "--address", 
        type=str, 
        default="http://localhost:8089",
        help="Address of the Order Agent server (default: http://localhost:8089)"
    )

    args = parser.parse_args()
    
    # Create the client
    client = OrderAgentClient(args.address)
    
    # Send the task and poll for completion
    task_status = await client.send_task()
    
    if task_status:
        logger.info(f"Final task state: {task_status.state}")
        if task_status.state == TaskState.completed:
            logger.info("Test passed: Task completed successfully")
            return 0
        elif task_status.state == TaskState.input_required:
            logger.info("Test passed: Task requires input (this is expected in some cases)")
            return 0
        else:
            logger.error(f"Test failed: Task ended with state {task_status.state}")
            return 1
    else:
        logger.error("No task status received")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        sys.exit(1)
