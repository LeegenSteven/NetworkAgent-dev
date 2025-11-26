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
import os
from .validatedesignprompt import validate_design_prompt

validate_design_agent = LlmAgent(
    name="ValidateDesignAgent",
    model="gemini-2.5-pro",
    instruction=validate_design_prompt,
    description="Validate that the generated change requests has enough information to be executable.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(
                url=os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080/sse")
            ),
            tool_filter=['getServiceDefinitions', 'getNetworkDesign']
        )
    ],
    output_key="validation_result"
)