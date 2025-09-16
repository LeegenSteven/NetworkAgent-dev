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
from typing import List
from .details_prompt import details_prompt
from resolveragents.utils.db import update_database
from resolveragents.utils.notification import notify_supervisor

logger = logging.getLogger(__name__)

class Node(BaseModel):
    """A ComputeInstance Node to be investigated"""
    id: str = Field(description="id of the computeinstance")
    name: str = Field(description="name of the computeinstance")
    configuration: str = Field(description="the computeinstance configuration ")
    status: str = Field(description="the computeinstance status")

class IncidentDetails(BaseModel):
    """The list of nodes to be investigated"""
    affectedNode: Node = Field(description="the compute instance directly where the incident originated")
    connectedNodes: List[Node] = Field(description="any related computeinstances that should be also investigated")

###################################################
# Incident Details Agent
###################################################
incident_details_agent = LlmAgent(
    name="IncidentDetailsAgent",
    model="gemini-2.0-flash",
    instruction=details_prompt,
    description="Identifies the root cause to a network incident.",
    output_schema=IncidentDetails,
    output_key='strategy',
    after_agent_callback=[notify_supervisor, update_database],
    disallow_transfer_to_parent=True, 
    disallow_transfer_to_peers=True,
)