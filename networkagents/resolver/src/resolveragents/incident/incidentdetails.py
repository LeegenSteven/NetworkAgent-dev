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
import logging
from pydantic import BaseModel, Field
from typing import TypedDict, Annotated, Tuple, List, Any

logger = logging.getLogger(__name__)

class Node(BaseModel):
    """Whether the user has confirmed the plan proposed by the agent or not"""
    name: str = Field(description="")
    kind: str = Field(description="")

class IncidentDetails(BaseModel):
    """Whether the user has confirmed the plan proposed by the agent or not"""
    affectedNode: Node = Field(description="")
    childrenNodes: List[Node] = Field(description="")
    connectedNodes: List[Node] = Field(description="")

###################################################
# Incident Details Agent
###################################################
incident_details_agent = LlmAgent(
    name="IncidentDetailsAgent",
    model="gemini-2.0-flash",
    instruction="""You are an expert network operations dude.""",
    description="Identifies the root cause to a network incident.",
    output_schema=IncidentDetails,
    disallow_transfer_to_parent=True, 
    disallow_transfer_to_peers=True,
)