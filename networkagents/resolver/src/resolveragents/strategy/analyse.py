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

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
import logging
import os
from .analyse_prompt import analyse_prompt

logger = logging.getLogger(__name__)


###################################################
# Incident Details Agent
###################################################
analyse_incident_agent = LlmAgent(
    name="AnalyseIncidentAgent",
    model="gemini-2.5-flash",
    instruction=analyse_prompt,
    description="Gathers incident information about the affected network services, locations and resources.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(
                url=os.getenv("TOOLS_URL")
            ),
            tool_filter=['get_node_details', 'get_connected_nodes', 'get_node_path','get_nodes_networking_by_kind']
        )
    ],
    output_key='initial_information'
)