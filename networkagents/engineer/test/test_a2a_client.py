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
import os
import sys
import json
from typing import Any
from uuid import uuid4
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    Task,
    MessageSendParams,
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

        async with httpx.AsyncClient() as httpx_client:
            card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=self.address)
            self.agent_card = await card_resolver.get_agent_card()
            self.agent_card.url = self.address        
            logger.info(f"Connected to agent: {self.agent_card.name}")

        self.agent_client = await A2AClient.get_client_from_agent_card_url(httpx_client=httpx.AsyncClient(), base_url=self.address)
        # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
        self.agent_client.url=self.address

        return self.agent_client

    def create_send_message_payload(self, text: str, task_id: str | None = None, context_id: str | None = None, isData: bool | None = False ) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", text, task_id, context_id)

        if isData:
            payload: dict[str, Any] = {
                'message': {
                    'role': 'user',
                    'parts': [{'type': 'data', 'data': {'objective': text }}],
                    'messageId': uuid4().hex,
                },
            }
        else:
            payload: dict[str, Any] = {
                'message': {
                    'role': 'user',
                    'parts': [{'type': 'text', 'text': text}],
                    'messageId': uuid4().hex,
                },
            }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload

    async def send_task(self, task_text: str, use_data_part=False):
        """
        Send a task to the Engineer Agent.
        
        Args:
            task_text: The text of the task to send.
            use_data_part: If True, send the task as a data part with {"objective": task_text}.
                          If False, send the task as a text part.
            
        Returns:
            The final task status or None if an error occurred.
        """
        if not self.agent_client:
            await self.create_client()
            
        logger.info(f"Sending task to Engineer Agent: {task_text}")
        logger.info(f"Using data part: {use_data_part}")
        
        # Create a message with either text or data part
        send_payload=self.create_send_message_payload(text=task_text, isData=use_data_part)

        # Create the request
        request = SendStreamingMessageRequest(
            params=MessageSendParams(**send_payload)
        )
        
        try:
            if self.agent_card.capabilities.streaming:
                task_status = None
                
                logger.info("Starting streaming request...")
                async for chunk in self.agent_client.send_message_streaming(request):
                    logger.info(f"Received chunk: {chunk}")
                    
                    if isinstance(chunk.root, SendStreamingMessageSuccessResponse):
                        if isinstance(chunk.root.result, Task):
                            task = chunk.root.result
                            logger.info(f"Task created with ID: {task.id}")
                            
                        elif isinstance(chunk.root.result, TaskArtifactUpdateEvent):
                            artifact = chunk.root.result.artifact
                            logger.info(f"Artifact update: {artifact.parts[0].root.text}")
                            
                        elif isinstance(chunk.root.result, TaskStatusUpdateEvent):
                            task_status = chunk.root.result.status
                            
                            if task_status.state == TaskState.failed:
                                error_message = "Task failed"
                                if (task_status.message and task_status.message.parts and 
                                    task_status.message.parts[0].root.text):
                                    error_message = task_status.message.parts[0].root.text
                                logger.error(f"Task failed: {error_message}")
                                
                            elif task_status.state == TaskState.input_required:
                                logger.info("Task requires input")
                                # In a real test, you might want to handle this case
                                
                            elif task_status.state == TaskState.working:
                                if task_status.message and task_status.message.parts:
                                    logger.info(f"Task working: {task_status.message.parts[0].root.text}")
                                
                            elif task_status.state == TaskState.completed:
                                logger.info("Task completed")
                                if task_status.message and task_status.message.parts:
                                    logger.info(f"Final result: {task_status.message.parts[0].root.text}")
                
                return task_status
            else:
                logger.error("Engineer Agent does not support streaming")
                return None
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Engineer Agent: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error communicating with Engineer Agent: {str(e)}", exc_info=True)
            return None

async def main():
    """Main function to run the test."""
    parser = argparse.ArgumentParser(description="Test the Engineer Agent using A2A client")
    parser.add_argument(
        "--address", 
        type=str, 
        default="http://localhost:8081",
        help="Address of the Engineer Agent server (default: http://localhost:8081)"
    )
    parser.add_argument(
        "--task", 
        type=str, 
        default="Create a network service for connecting two locations",
        help="Task to send to the Engineer Agent"
    )
    parser.add_argument(
        "--use-data-part",
        action="store_true",
        help="Send the task as a data part with {'objective': task_text} instead of a text part"
    )
    
    args = parser.parse_args()
    
    # Create the client
    client = EngineerAgentClient(args.address)
    
    # Send the task
    task_status = await client.send_task(args.task, use_data_part=args.use_data_part)
    
    if task_status:
        logger.info(f"Final task state: {task_status.state}")
        if task_status.state == TaskState.completed:
            logger.info("Test passed: Task completed successfully")
            return 0
        else:
            logger.error(f"Test failed: Task ended with state {task_status.state}")
            return 1
    else:
        logger.error("Test failed: No task status received")
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
