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
import json
from aiohttp_cors.cors_config import CorsConfig
import aiohttp_cors
from aiohttp import web
from agent.host_agent import HostAgent
from endpoints.socketendpoint import SocketEndpoint

logger = logging.getLogger(__name__)

class RestEndpoint:

    _instance = None

    def __init__(self, app: web.Application, cors: CorsConfig):
        logger.info("RestEndpoint init")

        RestEndpoint._instance = self

        self.app = app
        self.cors = cors
        corsConfig = {
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        }

        addAgentRoute = self.app.router.add_post("/addagent", self.addAgent)
        self.cors.add(addAgentRoute, corsConfig)

        listAgentsRoute = self.app.router.add_get("/listagents", self.listAgents)
        self.cors.add(listAgentsRoute, corsConfig)

        deleteAgentRoute = self.app.router.add_post("/deleteagent", self.deleteAgent)
        self.cors.add(deleteAgentRoute, corsConfig)

        pushNotificationRoute = self.app.router.add_post("/pushnotification", self.pushNotification)
        self.cors.add(pushNotificationRoute, corsConfig)

    #################################################################
    # Add a remote agent
    #################################################################
    async def addAgent(self, request):
        logger.info("REST endpoint: add remote agent")
        try:
            # Get the URL of the agent to add from the request
            data = await request.json()
            if 'url' not in data:
                logger.error("No URL provided in add_remote_agent request")
                return web.json_response(
                    {"error": "No URL provided in request"},
                    status=400
                )
                
            url = data['url']
            logger.info(f"Adding agent with URL: {url}")
            
            # Get the HostAgent instance and add the remote agent
            agent = await HostAgent.get_instance()
            agent_data = await agent.add_remote_agent(url)
            
            if agent_data:
                logger.info(f"Successfully added agent: {agent_data}")
                
                # Return the agent data
                return web.json_response(agent_data)
            else:
                logger.error(f"Failed to add agent with URL: {url}")
                return web.json_response(
                    {"error": f"Failed to add agent with URL: {url}"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error adding remote agent: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error adding remote agent: {str(e)}"},
                status=500
            )
    
    #################################################################
    # List all added remote agents
    #################################################################
    async def listAgents(self, request):
        logger.info("REST endpoint: list all remote agents")
        try:
            agent = await HostAgent.get_instance()
            remote_agents = agent.list_all_remote_agents()
            logger.info(f"Returning {len(remote_agents)} remote agents: {remote_agents}")
            
            # Return the list of remote agents
            return web.json_response(remote_agents)
        except Exception as e:
            logger.error(f"Error listing remote agents: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error listing remote agents: {str(e)}"},
                status=500
            )

    #################################################################
    # Delete an agent
    #################################################################
    async def deleteAgent(self, request):
        logger.info("REST endpoint: delete remote agent")
        try:
            # Get the URL of the agent to delete from the request
            data = await request.json()
            if 'url' not in data:
                logger.error("No URL provided in delete_remote_agent request")
                return web.json_response(
                    {"error": "No URL provided in request"},
                    status=400
                )
                
            url = data['url']
            logger.info(f"Deleting agent with URL: {url}")
            
            # Get the HostAgent instance and delete the remote agent
            agent = await HostAgent.get_instance()
            success = await agent.delete_remote_agent(url)
            
            if success:
                # Get the updated list of remote agents
                remote_agents = agent.list_remote_agents()
                logger.info(f"Successfully deleted agent with URL: {url}")
                logger.info(f"Remaining agents: {remote_agents}")
                
                # Return the updated list of remote agents
                return web.json_response(remote_agents)
            else:
                logger.error(f"Failed to delete agent with URL: {url}")
                return web.json_response(
                    {"error": f"Failed to delete agent with URL: {url}"},
                    status=404
                )
        except Exception as e:
            logger.error(f"Error deleting remote agent: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error deleting remote agent: {str(e)}"},
                status=500
            )

    #################################################################
    # Callback for agent notifications
    #################################################################
    async def pushNotification(self, request):
        """
        Handle push notification requests from agents.
        
        Notification request has the following structure:
        {
            "state": "input_required",
            "task_id": "",
            "context_id": "",
            "content": "",
            "input_data": ""
        }
        
        Args:
            request: The HTTP request object
            
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("Received agent task update")

        try:
            # Validate request has JSON content
            if not request.can_read_body:
                logger.error("Request has no body")
                return web.json_response(
                    {"error": "Request body is required"},
                    status=400
                )
                
            # Get the request data
            try:
                data = await request.json()
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in request: {str(e)}")
                return web.json_response(
                    {"error": f"Invalid JSON in request: {str(e)}"},
                    status=400
                )
                
            # Validate required fields
            required_fields = ["name", "state", "task_id", "context_id", "content", "input_data"]
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                logger.error(f"Missing required fields: {missing_fields}")
                return web.json_response(
                    {"error": f"Missing required fields: {missing_fields}"},
                    status=400
                )

            # Send notification to all connected sockets
            success = await SocketEndpoint._instance.sendPushNotification(data)
            
            if success:
                logger.info("Successfully sent notification to all connected clients")
                return web.json_response({"status": "success"})
            else:
                logger.error("Failed to send notification to all connected clients")
                return web.json_response(
                    {"error": "Failed to send notification to all connected clients"},
                    status=500
                )

        except Exception as e:
            logger.error(f"Error processing push notification: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error processing push notification: {str(e)}"},
                status=500
            )
