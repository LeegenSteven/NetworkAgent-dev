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

from google.adk.agents import SequentialAgent
from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from .subagents import network_design_agent,validate_design_agent,execute_design_agent
import logging
from typing import ClassVar
from agent_library.agentmiddleware.adk import ADKAgent
from agent_library.trace.trace_plugin import TracePlugin

logger = logging.getLogger(__name__)

class OrderAgent:
    """
    The order agent.
    """
    SUPPORTED_CONTENT_TYPES: ClassVar[list[str]] = ['text', 'text/plain']

    # static agent instance
    _instance = None

    @classmethod
    async def get_instance(cls):
        if OrderAgent._instance is None:
            OrderAgent._instance = cls()
        return OrderAgent._instance

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        import descriptions

        self.app_name = "OrderAgent"
        
        self.root_agent = SequentialAgent(
            name=self.app_name,
            description=descriptions.description,
            sub_agents=[network_design_agent],#, validate_design_agent, execute_design_agent]
        )

        # Initialize ADKAgent wrapper for AG-UI protocol support
        # Let ADKAgent manage its own session and artifact services
        self.adk_agent = ADKAgent(
            adk_agent=self.root_agent,
            app_name=self.app_name,
            use_in_memory_services=True
        )
    
        self.runner = Runner(
            app_name="OrderAgent",
            agent=self.root_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()]
        )     
