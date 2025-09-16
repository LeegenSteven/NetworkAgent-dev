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

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.runnables.config import RunnableConfig
from collections.abc import AsyncIterable
from typing import TypedDict, Annotated, Any, Dict, Optional
from langgraph.graph.message import add_messages
from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AnyMessage, ToolMessage, HumanMessage
import agent.prompts as prompts
from utils.credentials import get_credentials
from utils.error_handler import (
    TestAgentError,
    ToolError,
    ErrorSeverity
)
import logging
import os
import json
import asyncio

logger = logging.getLogger(__name__)

class TestAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

class TestAgent:

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    _instance = None

    @classmethod
    async def get_instance(cls):
        if TestAgent._instance is None:
            TestAgent._instance = cls()
            await TestAgent._instance.load_tools()
        return TestAgent._instance

    def __init__(self):
        logger.info("starting ops agent")

        self.credentials = get_credentials()

        workflow = StateGraph(TestAgentState)
        workflow.add_node("call_model", self.call_model)
        workflow.add_node("tool_node", self.tool_node)
        workflow.add_edge(START, "call_model")
        workflow.add_edge("tool_node", "call_model")
        workflow.add_conditional_edges("call_model", self.should_run_tool, ["tool_node", END])

        checkpointer = InMemorySaver()
        self.operationsAgentApp = workflow.compile(checkpointer=checkpointer)

    async def load_tools(self):
        """
        Initialise the MCP tools with retry logic to ensure successful loading
        """
        logger.info("loading tools")
        agent_mcp_tool_address = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080")
        
        # Initialize the MCP client
        self.mcpClient = MultiServerMCPClient(
            {
                "networkagent": {
                    "url": f"{agent_mcp_tool_address}/sse",
                    "transport": "sse",
                }
            }
        )

        try:
            # names of the tools this agent can use
            allowed_tools_names=["getRunningTests","runTest","deleteTest"]
            # load all tools
            self.tools = await self.mcpClient.get_tools()
            self.allowed_tools=[]
            for t in self.tools:
                if t.name in allowed_tools_names:
                    self.allowed_tools.append(t)
            self.tools_by_name = {tool.name: tool for tool in self.allowed_tools}

            logger.info(f"Successfully loaded {len(self.tools)} tools")
            logger.debug(f"Loaded tools: {self.tools_by_name}")                    

        except asyncio.exceptions.CancelledError as e:
            logger.warning(f"Error running tools: {str(e)}")
            raise ToolError(
                message="Failed to run tool",
                tool_name="discover crds and locations tools",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
        except Exception as e:
            logger.error(f"Failed to load tools")
            raise ToolError(
                message=f"Failed to load tools",
                tool_name="load_tools",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

    async def call_model(self, state: TestAgentState):
        """
        Run the model
        """
        logger.info("run incident helper")

        try:
            prompt = ChatPromptTemplate(
                [
                    ("system", prompts.operations_prompt), 
                    ("placeholder", "{messages}")
                ]
            )

            model = ChatVertexAI(
                model_name="gemini-2.5-flash",
                temperature=0,
                credentials=self.credentials,
                project=os.getenv("GOOGLE_PROJECT"),
                location=os.getenv("GOOGLE_REGION")
            )
            model = model.bind_tools(self.allowed_tools)

            runnable = prompt | model
            response = await runnable.ainvoke({"messages": state['messages']})

            return {'messages': response}
        except Exception as e:
            logger.error(f"Error in call_model: {str(e)}", exc_info=True)
            raise TestAgentError(
                message=f"Error running model: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

    async def should_run_tool(self, state: TestAgentState):
        logger.info("should continue to tools or end")

        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            logger.info("run tool")
            return "tool_node"

        logger.info("finish")
        return END

    async def tool_node(self, state: TestAgentState):
        """
        Call incident tools and return output
        """
        logger.info("calling tool")

        outputs = []

        for tool_call in state["messages"][-1].tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            
            try:
                if tool_name not in self.tools_by_name:
                    raise ToolError(
                        message=f"Tool '{tool_name}' not found",
                        tool_name=tool_name,
                        tool_args=tool_args,
                        severity=ErrorSeverity.ERROR
                    )
                    
                tool_result = await self.tools_by_name[tool_name].ainvoke(tool_args)
                outputs.append(
                    ToolMessage(
                        content=json.dumps(tool_result),
                        name=tool_name,
                        tool_call_id=tool_call["id"],
                    )
                )
            except asyncio.exceptions.CancelledError as e:
                logger.warning(f"Error running tools: {str(e)}")
                raise ToolError(
                    message="Failed to run tool",
                    tool_name="discover crds and locations tools",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
            except Exception as e:
                # Convert other exceptions to ToolError
                error = ToolError(
                    message=f"Error executing tool {tool_name}: {str(e)}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                logger.error(f"Error executing tool {tool_name}: {str(e)}", exc_info=True)
                outputs.append(
                    ToolMessage(
                        content=json.dumps({"error": error.message, "details": error.details}),
                        name=tool_name,
                        tool_call_id=tool_call["id"],
                    )
                )
                
            return {"messages": outputs}

    async def stream(self, query: str, sessionId: str) -> AsyncIterable[dict[str, Any]]:
        """
        Bridge between A2A stream and langgraph stream
        """
        inputs: dict[str, Any] = {'messages': [('user', query)]}
        config: RunnableConfig = {'configurable': {'thread_id': sessionId}}

        logger.info("langgraph stream started with %s and thread id %s", query, sessionId)

        try:
            response = await self.operationsAgentApp.ainvoke(inputs, config=config)
            return response['messages'][-1].content
        except Exception as e:
            logger.error(f"Error in stream: {str(e)}", exc_info=True)
            raise TestAgentError(
                message=f"Error in stream processing: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
