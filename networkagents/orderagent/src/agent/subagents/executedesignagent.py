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
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from .executedesignprompt import execute_design_prompt
from google.adk.agents.callback_context import CallbackContext
from typing import Optional
from google.genai import types # For types.Content
import logging

logger = logging.getLogger(__name__)

class ExecuteDesignOutput(BaseModel):
    """Decision to execute the change request"""
    decision: bool = Field(description="Boolean decision to proceed to execute the change request")

async def execute_design_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    """Notify the supervisor of the change request"""
    logger.info("Sending change request to engineer design agent")
    decision = callback_context.state['execute_design_output']['decision']
    if decision:
        logger.info("Change request approved")
    else:
        logger.info("Change request denied")
    return None

# execute the change if the result of the previous result is approved
execute_design_agent = LlmAgent(
    name="ExecuteDesignAgent",
    model="gemini-2.0-flash",
    instruction=execute_design_prompt,
    description="Execute the generated change request",
    output_schema=ExecuteDesignOutput,
    output_key="execute_design_output",
    after_agent_callback=[execute_design_callback],
    disallow_transfer_to_parent=True, 
    disallow_transfer_to_peers=True,
)