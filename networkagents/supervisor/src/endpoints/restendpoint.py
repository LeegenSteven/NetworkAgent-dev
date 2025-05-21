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
from aiohttp_cors.cors_config import CorsConfig
import aiohttp_cors
from aiohttp import web

logger = logging.getLogger(__name__)

class RestEndpoint:
    def __init__(self, app: web.Application, cors: CorsConfig):
        logger.info("RestEndpoint init")

        self.app = app
        cors = {
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        }

        addAgentRoute = self.app.router.add_post("/addagent", self.addAgent)
        # self.cors.add(addAgentRoute, cors)

        agentNotificationRoute = self.app.router.add_post("/agentnotification", self.agentNotification)

    #################################################################
    # Add a remote agent
    #################################################################
    async def addAgent(self, request):
        logger.info("add agent with url")

        if 'url' in request.match_info:
            agent_url = request.match_info['url']

        return web.json_response()
    
    #################################################################
    # List all added remote agents
    #################################################################
    async def listAgents(self):
        pass

    #################################################################
    # Delete an agent
    #################################################################
    async def deleteAgent(self):
        pass

    #################################################################
    # Callback for agent notifications
    #################################################################
    async def agentNotification(self, request):
        logger.info("received agent notification")
        pass