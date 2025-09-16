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
import json
import os
import requests
from datetime import datetime
from typing import Any
from uuid import uuid4
from google.cloud import spanner
import google.auth
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendMessageRequest,
    SendMessageSuccessResponse,
    TaskState,
    Task,
    MessageSendParams,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
)
logger = logging.getLogger(__name__)

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

# ------------------------------------------
# Build a serialized JSON representation of the 
# body that fit into a INSERT/UPDATE SQL statement
#
# **WARNING** Please think twice before making modifications
# here as it took me a lot of trial and errors to come up
# with this solution
# ------------------------------------------
def body_sql_json_dump(string_dump):
    # Double escape the \" sequences created by the santitize call so as to build
    # a syntactically correct SQL INSERT statement for Spanner to execute.
    # Also escape single quotes as single quotes are used to enclose the
    # JSON string in the SQL statement.
    return string_dump.replace('\\n','\\\\n').replace('\\"', '\\\\"').replace("'", "\\'")

class ResolverAgentClient:
    """A client to connect to and test the Resolver Agent."""

    def __init__(self, address: str, supervisor_url: str = None):
        """
        Initialize the Resolver Agent client.
        
        Args:
            address: The address of the Resolver Agent server.
            supervisor_url: The URL of the Supervisor Agent for notifications.
        """
        self.address = address
        self.supervisor_url = supervisor_url
        self.agent_card = None
        self.agent_client = None
        self.database = None

    async def create_client(self):
        """Create an A2A client to connect to the Resolver Agent."""
        logger.info(f"Creating client for Resolver Agent with address {self.address}")

        async with httpx.AsyncClient(timeout=60.0) as httpx_client:
            card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=self.address)
            self.agent_card = await card_resolver.get_agent_card()
            self.agent_card.url = self.address
            logger.info(f"Connected to agent: {self.agent_card.name}")

        self.agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=60.0), url=self.address)

        # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
        self.agent_client.url=self.address

    def create_send_message_payload(self, data: dict, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", data, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'data','data': data}],
                'messageId': uuid4().hex,
                'kind': 'message'
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload

    def get_credentials(self):
        try:
            credentials=google.auth.load_credentials_from_file("../src/networkagent.json")[0]
            logger.info(f"Successfully loaded application default credentials")
            return credentials
        except Exception as e:
            logger.error(f"Error loading application default credentials: {e}")
            return None

    async def spanner_connect(self):
        logger.info("Spanner connect")
        if self.database is None:
            try:
                credentials = self.get_credentials()
                if credentials is None:
                    logger.warning("No credentials available, will use simulation mode")
                    return False
                    
                spanner_client = spanner.Client(credentials=credentials)
                instance = spanner_client.instance(SPANNER_INSTANCE)
                self.database = instance.database(SPANNER_DATABASE)
                logger.info("Successfully connected to Spanner database")
                return True
            except Exception as e:
                logger.warning(f"Failed to connect to Spanner database: {e}")
                logger.info("Will use simulation mode for incident creation")
                return False

    async def send_notification(self, taskid, incident_data):
        """Send notification to supervisor for incident update."""
        logger.info("Sending notification to supervisor for incident update")
        # Use provided supervisor URL, fallback to environment variable, then default
        supervisor_url = os.getenv("SUPERVISOR_URL", "http://127.0.0.1:9000")
        if not supervisor_url:
            logger.error("No supervisor URL provided and SUPERVISOR_URL environment variable not set")
        else:
            notification_url = f"{supervisor_url}/pushnotification"
            # Create the payload using incident_update state (replaces new_incident)
            payload = {
                "name": "Resolver Agent Test",
                "state": "incident_update",
                "task_id": taskid,
                "context_id": taskid,
                "content": "Resolution progress update",
                "input_data": {
                    "incident_data": incident_data,
                    "strategy": None,  # No strategy yet in initial notification
                    "root_case": None,  # No root cause yet in initial notification
                    "resolution": None,  # No resolution yet in initial notification
                }
            }
            
            try:
                # Send the POST request
                logger.info(f"Sending notification to {notification_url}")
                response = requests.post(notification_url, json=payload)
                
                # Check if the request was successful
                if response.status_code == 200:
                    logger.info("Notification sent successfully")
                else:
                    logger.error(f"Failed to send notification. Status code: {response.status_code}")
                    logger.error(f"Response: {response.text}")
            except Exception as e:
                logger.error(f"Error sending notification: {str(e)}", exc_info=True)

    async def create_incident(self, incident_data, task_id):
        """Creates an incident in the database."""
        logger.info(f"creating incident {incident_data} {task_id} ")

        # Ensure database connection
        if self.database is None:
            connection_success = await self.spanner_connect()
            if not connection_success:
                raise Exception("Failed to connect to Spanner database - test cannot continue")

        # create spanner compatible json
        incident_json = json.dumps(incident_data, ensure_ascii=True)
        incident_json_spanner = body_sql_json_dump(incident_json)
        logger.info(incident_json_spanner)

        upsert_template = "INSERT OR UPDATE Incident (id, recordedTimestamp, agentTaskId, issue) VALUES ('{id}', {timestamp}, '{task_id}', JSON '{issue}')"
        # Use UTC timestamp in milliseconds to match dashboard expectations
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        upsert = upsert_template.format(id=task_id, timestamp=timestamp_ms, task_id=task_id, issue=incident_json_spanner)
        logger.info(upsert)

        try:
            def insert_incident(transaction):
                transaction.execute_update(upsert)

            self.database.run_in_transaction(insert_incident)

            logger.info(f"Created incident: {task_id    }")
            
            # Send notification to supervisor after successfully creating incident
            await self.send_notification(task_id, incident_data)
            
            return task_id

        except Exception as e:
            logger.error(f"Error creating incident: {e}")
            raise Exception(f"Failed to create incident in database: {e}")

    async def send_task(self, task_text: str):
        """
        Send a task to the Resolver Agent.
        
        Args:
            task_text: The text of the task to send.
            
        Returns:
            The final task status or None if an error occurred.
        """
        if not self.agent_client:
            await self.create_client()
            
        logger.info(f"Sending task to Resolver Agent: {task_text}")

        # create incident id
        incidentid = uuid4().hex

        gnb_incident = {'incident': {'incident_id': incidentid, 'error': 'CRITICAL: Process ./nr-gnb is not running on host cellsite1-ueransim', 'hostname': 'cellsite1-ueransim', 'process_name': './nr-gnb'}}
        connection_incident = {'incident': {'incident_id': incidentid, 'error': "URL is not accessible - connection failed: HTTPConnectionPool(host='172.168.0.2', port=80): Max retries exceeded with url: / (Caused by ConnectTimeoutError(<urllib3.connection.HTTPConnection object at 0x778734559240>, 'Connection to 172.168.0.2 timed out. (connect timeout=5)'))", 'node': 'cellsite1-ueransim', 'url': 'http://172.168.0.2', 'userid': '208930000000002'}}

        # Create a dummy incident in the database like in the fault service
        try:
            incident_created = await self.create_incident(connection_incident, incidentid)
            logger.info(f"Successfully created incident for task: {incidentid}")
        except Exception as e:
            logger.error(f"Failed to create incident: {e}")
            raise Exception(f"Test failed due to incident creation failure: {e}")

        # Create a message with data part
        send_payload = self.create_send_message_payload(data=connection_incident, context_id=incidentid, task_id=None)
        params = MessageSendParams(**send_payload)
                
        logger.info(f"Request parameters: {params}")

        try:
            logger.info("Sending non-streaming request ...")
            
            request = SendMessageRequest(id = str(uuid4()),params=params)            
            response = await self.agent_client.send_message(request)
            logger.info(response)
            
            if isinstance(response.root, SendMessageSuccessResponse):
                if isinstance(response.root.result, Task):
                    task = response.root.result
                    task_id = task.id
                    logger.info(f"Task created with ID: {task_id}")                    
                    return task
                else:
                    logger.error("Response did not contain a Task object")
                    return None
            else:
                logger.error(f"Unexpected response: {response}")
                return None
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Agent: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error communicating with Agent: {str(e)}", exc_info=True)
            return None

async def main():
    """Main function to run the test."""
    parser = argparse.ArgumentParser(description="Test the Resolver Agent using A2A client")
    parser.add_argument(
        "--address", 
        type=str,
        default="http://127.0.0.1:8099",
        help="Address of the Resolver Agent server (default: http://127.0.0.1:8099)"
    )
    parser.add_argument(
        "--task", 
        type=str, 
        default="Do something to fix the network",
        help="Task to send to the Resolver Agent"
    )
    parser.add_argument(
        "--supervisor-url",
        type=str,
        default="http://127.0.0.1:9000",
        help="URL of the Supervisor Agent for notifications (default: http://127.0.0.1:9000)"
    )
    
    args = parser.parse_args()
    
    # Create the client
    client = ResolverAgentClient(args.address, args.supervisor_url)
    
    # Send the task and poll for completion
    task_status = await client.send_task(
        args.task
    )
    
    logger.info("Test passed: Task completed successfully")

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
