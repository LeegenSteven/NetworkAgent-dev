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

import os
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse, LlmRequest
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from typing import Optional
from google.genai import types 
import logging

logger = logging.getLogger(__name__)


###################################################
# RCA before model callback
###################################################
def root_cause_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    agent_name = callback_context.agent_name
    logger.info(f"[Callback] Before model call for agent: {agent_name}")

###################################################
# RCA Agent
###################################################
troubleshoot_agent = LlmAgent(
    name="TroubleShootAgent",
    model="gemini-2.0-flash",
    instruction="""You are an expert network operations dude.""",
    description="Identifies the root cause to a network incident.",
    before_model_callback=root_cause_callback,
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(
                url=os.getenv("TOOLS_URL")
            ),
            tool_filter=['fetch_all_metrics', 'getNodePath']
        )
    ],
    output_key="root_cause",
)