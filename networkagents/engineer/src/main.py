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
from agent.agent_executor import EngineerAgentExecutor
from agent.network_engineer_agent import NetworkEngineerAgent
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, InMemoryPushNotifier
from a2a.server.apps import A2AStarletteApplication
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import uvicorn
import descriptions

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

def get_agent_card(host: str, port: int):
    """Returns the Agent Card for the Engineering Agent."""
    capabilities = AgentCapabilities(streaming=True, pushNotifications=True)

    chat_skill = AgentSkill(
        id='network_engineer_agent_chat',
        name='Chat Engineer Support',
        description=descriptions.chat_description,
        tags=descriptions.chat_tags,
        examples=descriptions.chat_examples
    )

    background_skill = AgentSkill(
        id='network_engineer_agent_background',
        name='Remote Agent Engineer Support',
        description=descriptions.background_description,
        tags=descriptions.background_tags,
        examples=descriptions.background_examples
    )

    return AgentCard(
        name='Network Engineer Agent',
        description=descriptions.description,
        url=f'http://{host}:{port}/',
        version='1.0.0',
        defaultInputModes=NetworkEngineerAgent.SUPPORTED_CONTENT_TYPES,
        defaultOutputModes=NetworkEngineerAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[chat_skill, background_skill],
    )


if __name__ == "__main__":
    logger.info("starting network engineer agent server...")

    # init the agent class
    request_handler = DefaultRequestHandler(
        agent_executor=EngineerAgentExecutor(),
        task_store=InMemoryTaskStore(),
        push_notifier=InMemoryPushNotifier(httpx_client=httpx.AsyncClient())
    )

    host = "0.0.0.0"
    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 8081

    server = A2AStarletteApplication(
        agent_card=get_agent_card(host, port), http_handler=request_handler
    )
    uvicorn.run(server.build(), host=host, port=port)
