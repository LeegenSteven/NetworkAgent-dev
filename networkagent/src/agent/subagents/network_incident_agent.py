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

import google.auth
import logging
import os
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.graph import StateGraph, END, START
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage, AIMessage
from agent.tools.engineering import getRunningTests, runTest, deleteTest
import agent.prompts.incident as incident_prompts
from utils.k8s import get_credentials
import json

logger = logging.getLogger(__name__)

if logger.getEffectiveLevel() == logging.DEBUG:
  from langchain.globals import set_debug, set_verbose
  set_debug(True)
  set_verbose(False)

class IncidentAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

class IncidentAgent:
    _instance = None

    @staticmethod
    def get_instance():
        if IncidentAgent._instance is None:
            IncidentAgent._instance = IncidentAgent()
        return IncidentAgent._instance

    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        self.credentials = get_credentials()

        self.tools=[getRunningTests, runTest, deleteTest]
        self.tools_by_name = {tool.name: tool for tool in self.tools}

        workflow = StateGraph(IncidentAgentState)
        workflow.add_node("call_model", self.call_model)
        workflow.add_node("tool_node", self.tool_node)
        workflow.add_edge(START, "call_model")
        workflow.add_edge("tool_node", "call_model")
        workflow.add_conditional_edges("call_model", self.should_run_tool, ["tool_node", END])
        self.incidentAgentApp = workflow.compile()

    async def call_model(self, state: IncidentAgentState):
        """
        Run the model
        """
        logger.info("run incident helper")

        prompt = ChatPromptTemplate(
            [
                ("system", incident_prompts.incident_prompt), 
                ("placeholder", "{messages}")
            ]
        )

        model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
        model = model.bind_tools(self.tools)

        runnable = prompt | model
        response = await runnable.ainvoke({"messages": state['messages']})

        return {'messages': response}

    async def should_run_tool(self, state: IncidentAgentState):
        logger.info("should continue to tools or end")

        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            logger.info("run tool")
            return "tool_node"

        logger.info("finish")
        return END

    async def tool_node(self, state: IncidentAgentState):
        """
        Call incident tools and return output
        """
        logger.info("calling tool")
        outputs = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_result = self.tools_by_name[tool_call["name"]].invoke(tool_call["args"])
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
            
        return {"messages": outputs}


