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
import logging
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from google.genai import types
import os
import json

logger = logging.getLogger(__name__)

###################################################
# Post agent callback to update the incident DB
###################################################
async def update_database(callback_context: CallbackContext) -> Optional[types.Content]:
    logger.info("update database with incident information collected")

    # Check if state variables are None and only include non-None values
    strategy=None
    root_cause=None
    resolution=None

    incident=callback_context.state['incident_data']['incident']
    logger.info("updating incident %s", incident['incident_id'])
        
    if 'strategy' in callback_context.state:
        strategy = callback_context.state['strategy']
        logger.info("STRATEGY")
        logger.info("========")
        logger.info(json.dumps(strategy, indent=4))

    if 'root_cause' in callback_context.state:
        root_cause = callback_context.state['root_cause']
        logger.info("ROOT_CAUSE")
        logger.info("==========")
        logger.info(root_cause)

    if 'resolution' in callback_context.state:
        resolution = callback_context.state['resolution']
        logger.info("RESOLUTION")
        logger.info("==========")
        logger.info(resolution)

    # update the incident in spanner
    try:
        toolset=MCPToolset(
                connection_params=SseConnectionParams(
                    url=os.getenv("TOOLS_URL")
                ),
                tool_filter=['updateIncident']
            )
        logger.info(f"Connecting to MCP server at: {os.getenv('TOOLS_URL')}")
        
        tools = await toolset.get_tools()
        logger.info(f"Available tools: {[tool.name for tool in tools]}")
        
        if not tools:
            logger.error("No tools found matching filter 'updateIncident'")
            return None
            
        logger.info(f"Calling updateIncident with args: id={incident['incident_id']}, strategy={strategy is not None}, root_cause={root_cause is not None}, resolution={resolution is not None}")
        
        result = await tools[0].run_async(args={"id": incident['incident_id'], 
                                                "strategy": strategy if strategy is not None else {}, 
                                                "root_cause": root_cause, 
                                                "resolution": resolution
                                                }, 
                                          tool_context=None)
        logger.info(f"Tool execution result: {result}")
        
        await toolset.close()
        return result
        
    except Exception as e:
        logger.error(f"Error calling MCP tool: {e}")
        logger.exception("Full exception details:")
        return None
