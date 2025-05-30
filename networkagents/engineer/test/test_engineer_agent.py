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
from test_a2a_client import EngineerAgentClient
from a2a.types import TaskState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
)
logger = logging.getLogger(__name__)

class TestEngineerAgent(unittest.TestCase):
    """Test cases for the Engineer Agent using A2A client."""

    def setUp(self):
        """Set up the test environment."""
        self.engineer_address = os.getenv("ENGINEER_ADDRESS", "http://localhost:8081")
        self.client = EngineerAgentClient(self.engineer_address)
        
    async def async_test_send_task(self):
        """Test sending a task to the Engineer Agent."""
        task_text = "Create a network service for connecting two locations"
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
        """Test sending a more complex task to the Engineer Agent."""
        task_text = "Create a mesh network between three locations: New York, London, and Tokyo"
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
        """Test sending a task to the Engineer Agent using a data part."""
        task_text = "Create a network service for connecting two locations"
        task_status = await self.client.send_task(task_text, use_data_part=True)
        
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
        """Test sending a more complex task to the Engineer Agent using a data part."""
        task_text = "Create a mesh network between three locations: New York, London, and Tokyo"
        task_status = await self.client.send_task(task_text, use_data_part=True)
        
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
