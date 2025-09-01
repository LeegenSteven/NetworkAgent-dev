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

import unittest
import asyncio
import logging
import os
import sys
from test_a2a_client import ResolverAgentClient
from a2a.types import TaskState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
)
logger = logging.getLogger(__name__)

class TestResolverAgent(unittest.TestCase):
    """Test cases for the Engineer Agent using A2A client."""

    def setUp(self):
        """Set up the test environment."""
        self.agent_address = os.getenv("RESOLVER_ADDRESS", "http://localhost:8081")
        self.client = ResolverAgentClient(self.agent_address)
        
    async def async_test_send_task(self):
        """Test sending a task to the Agent."""
        task_text = "add some test text here...!!!"
        task_status = await self.client.send_task(task_text)
        
        # Check if we got a response
        self.assertIsNotNone(task_status, "No task status received")
        
        # Check if the task completed successfully
        self.assertEqual(
            task_status.state, 
            TaskState.completed, 
            f"Task did not complete successfully. Final state: {task_status.state}"
        )
        
        # Check if we got a message back
        self.assertIsNotNone(task_status.message, "No message in task status")
        self.assertGreater(len(task_status.message.parts), 0, "No message parts in task status")
        
        # Log the final message
        logger.info(f"Final message: {task_status.message.parts[0].root.text}")
        
        return task_status
        
    def test_send_task(self):
        """Run the async test."""
        loop = asyncio.get_event_loop()
        task_status = loop.run_until_complete(self.async_test_send_task())
        self.assertIsNotNone(task_status)

    async def async_test_send_complex_task(self):
        """Test sending a more complex task to the Agent."""
        task_text = "fix something even bigger text!!!"
        task_status = await self.client.send_task(task_text)
        
        # Check if we got a response
        self.assertIsNotNone(task_status, "No task status received")
        
        # For complex tasks, we might get input_required or completed
        self.assertIn(
            task_status.state, 
            [TaskState.completed, TaskState.input_required], 
            f"Task ended in unexpected state: {task_status.state}"
        )
        
        # Check if we got a message back
        self.assertIsNotNone(task_status.message, "No message in task status")
        self.assertGreater(len(task_status.message.parts), 0, "No message parts in task status")
        
        # Log the final message
        logger.info(f"Final message: {task_status.message.parts[0].root.text}")
        
        return task_status
        
    def test_send_complex_task(self):
        """Run the async complex task test."""
        loop = asyncio.get_event_loop()
        task_status = loop.run_until_complete(self.async_test_send_complex_task())
        self.assertIsNotNone(task_status)
        
    async def async_test_send_task_with_data_part(self):
        """Test sending a task to the Agent."""
        task_text = "more text"
        task_status = await self.client.send_task(task_text)
        
        # Check if we got a response
        self.assertIsNotNone(task_status, "No task status received")
        
        # Check if the task completed successfully
        self.assertEqual(
            task_status.state, 
            TaskState.completed, 
            f"Task did not complete successfully. Final state: {task_status.state}"
        )
        
        # Check if we got a message back
        self.assertIsNotNone(task_status.message, "No message in task status")
        self.assertGreater(len(task_status.message.parts), 0, "No message parts in task status")
        
        # Log the final message
        logger.info(f"Final message: {task_status.message.parts[0].root.text}")
        
        return task_status
        
    def test_send_task_with_data_part(self):
        """Run the async test with data part."""
        loop = asyncio.get_event_loop()
        task_status = loop.run_until_complete(self.async_test_send_task_with_data_part())
        self.assertIsNotNone(task_status)
        
    async def async_test_send_complex_task_with_data_part(self):
        """Test sending a more complex task to the Agent."""
        task_text = "..."
        task_status = await self.client.send_task(task_text)
        
        # Check if we got a response
        self.assertIsNotNone(task_status, "No task status received")
        
        # For complex tasks, we might get input_required or completed
        self.assertIn(
            task_status.state, 
            [TaskState.completed, TaskState.input_required], 
            f"Task ended in unexpected state: {task_status.state}"
        )
        
        # Check if we got a message back
        self.assertIsNotNone(task_status.message, "No message in task status")
        self.assertGreater(len(task_status.message.parts), 0, "No message parts in task status")
        
        # Log the final message
        logger.info(f"Final message: {task_status.message.parts[0].root.text}")
        
        return task_status
        
    def test_send_complex_task_with_data_part(self):
        """Run the async complex task test with data part."""
        loop = asyncio.get_event_loop()
        task_status = loop.run_until_complete(self.async_test_send_complex_task_with_data_part())
        self.assertIsNotNone(task_status)

if __name__ == "__main__":
    unittest.main()
