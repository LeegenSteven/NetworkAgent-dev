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

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from pydantic import BaseModel, Field
from typing import List
import os
import logging
from .topology_prompt import search_prompt, format_prompt

logger = logging.getLogger(__name__)

class Node(BaseModel):
    """A ComputeInstance Node"""
    id: str = Field(description="id of the computeinstance")
    name: str = Field(description="name of the computeinstance")
    status: str = Field(description="the computeinstance status")

class Edge(BaseModel):
    """A directed connection between Nodes"""
    start: Node = Field(description="a compute instance ")
    end: Node = Field(description="a compute instance")

class Network(BaseModel):
    """A network of nodes and edges"""
    nodes: List[Node] = Field(description = "array of nodes")
    edges: List[Edge] = Field(description = "array of edges")

format_agent=LlmAgent(
    name="TopologyFormatSubAgent",
    description="Sub agent responsible for formatting topology information",
    model="gemini-2.0-flash",
    instruction=format_prompt,
    output_schema=Network
)

search_agent=LlmAgent(
    name="TopologySearchSubAgent",
    description="Sub agent responsible for gatheric network topology information",
    model="gemini-2.5-flash",
    instruction=search_prompt,
    output_key='topology',
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(
                url=os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080/sse")
            ),
            tool_filter=["get_node_path", "get_connected_nodes"]
        )
    ],
)

topology_agent=SequentialAgent(
    name="TopologySubAgent",
    description="Sub agent responsible for gatheric network topology information",
    sub_agents=[search_agent, format_agent]
)

