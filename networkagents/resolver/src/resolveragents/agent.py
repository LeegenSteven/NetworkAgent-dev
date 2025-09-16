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
import logging
from .strategy.agent import incident_investigator_agent
from .troubleshoot.agent import troubleshoot_agent
from .resolution.agent import resolution_agent

logger = logging.getLogger(__name__)

class IncidentAgent:
    """
    The incident agent.
    """

    # static agent instance
    _instance = None

    @classmethod
    async def get_instance(cls):
        if IncidentAgent._instance is None:
            IncidentAgent._instance = cls()
        return IncidentAgent._instance

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        self.root_agent = SequentialAgent(
            name="IncidentSupervisorAgent",
            sub_agents=[incident_investigator_agent, troubleshoot_agent, resolution_agent ],
            description="A sequence of expert agents investigate a network incident.",
        )

        self.runner = Runner(
            app_name="IncidentSupervisorAgent",
            agent=self.root_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
        )        
