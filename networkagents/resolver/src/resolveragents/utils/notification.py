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

from google.adk.agents.callback_context import CallbackContext
from typing import Optional
from google.genai import types # For types.Content
from google.genai import types
import logging
import os
import requests

logger = logging.getLogger(__name__)

###################################################
# Notify Supervisor Agent of Resolution progress
###################################################
async def notify_supervisor(callback_context: CallbackContext) -> Optional[types.Content]:
    logger.info("Sending notification to supervisor for resolution progress")

    supervisor_url = os.getenv("SUPERVISOR_URL", "http://127.0.0.1:9000")
    notification_url = f"{supervisor_url}/pushnotification"
    
    
    if 'incident' in callback_context.state['incident_data']:
        incident=callback_context.state['incident_data']['incident']

        # Extract relevant information from callback context
        logger.info(incident)

        incident_id = incident['incident_id']

        # Create the payload
        payload = {
            "name": "Resolver Agent",
            "state": "incident_update",
            "task_id": incident_id,
            "context_id": incident_id,
            "content": "Resolution progress update",
            "input_data": {
                "incident_data": callback_context.state['incident_data'],
                "strategy": callback_context.state['strategy'] if 'strategy' in callback_context.state else None,
                "root_cause": callback_context.state['root_cause'] if 'root_cause' in callback_context.state else None,
                "resolution": callback_context.state['resolution'] if 'resolution' in callback_context.state else None,
            }
        }

        logger.info(payload)

        try:
            # Send the POST request
            logger.info(f"Sending notification to {notification_url}")
            response = requests.post(notification_url, json=payload)
            
            # Check if the request was successful
            if response.status_code == 200:
                logger.info("Notification sent successfully")
                return None
            else:
                logger.error(f"Failed to send notification. Status code: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error sending notification: {str(e)}", exc_info=True)
            return None

    else:
        logger.error("NO INCIDENT DATA")

