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
import random

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
        from asyncio import TimeoutError

        logger.info("loading tools")
        agent_mcp_tool_address = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080")
        
        max_retries = int(os.getenv("TOOL_LOAD_MAX_RETRIES", "5"))
        base_delay = float(os.getenv("TOOL_LOAD_BASE_DELAY", "1.0"))
        max_delay = float(os.getenv("TOOL_LOAD_MAX_DELAY", "30.0"))
        timeout = float(os.getenv("TOOL_LOAD_TIMEOUT", "10.0"))
        
        retry_count = 0
        last_exception = None
        
        while retry_count <= max_retries:
            try:
                if retry_count > 0:
                    logger.info(f"Retry attempt {retry_count}/{max_retries} for loading tools")
                
                # Initialize the MCP client
                self.mcpClient = MultiServerMCPClient(
                    {
                        "networkagent": {
                            "url": f"{agent_mcp_tool_address}/sse",
                            "transport": "sse",
                        }
                    }
                )
                
                # Set a timeout for the get_tools operation
                try:
                    self.tools = await asyncio.wait_for(self.mcpClient.get_tools(), timeout)
                    self.tools_by_name = {tool.name: tool for tool in self.tools}
                    logger.info(f"Successfully loaded {len(self.tools)} tools")
                    logger.debug(f"Loaded tools: {self.tools_by_name}")
                    return  # Success, exit the retry loop
                except TimeoutError:
                    logger.warning(f"Timeout while loading tools (attempt {retry_count+1}/{max_retries+1})")
                    raise ToolError(
                        message=f"Timeout while loading tools (attempt {retry_count+1}/{max_retries+1})",
                        tool_name="load_tools",
                        severity=ErrorSeverity.WARNING
                    )
                    
            except TestAgentError as e:
                # If it's already a TestAgentError, just track it and potentially retry
                last_exception = e
                retry_count += 1
                
                if retry_count <= max_retries:
                    # Calculate backoff delay with jitter to avoid thundering herd problem
                    delay = min(max_delay, base_delay * (2 ** (retry_count - 1)))
                    jitter = random.uniform(0, 0.1 * delay)  # 10% jitter
                    actual_delay = delay + jitter
                    
                    logger.warning(f"Failed to load tools: {e.message}. Retrying in {actual_delay:.2f} seconds...")
                    await asyncio.sleep(actual_delay)
                else:
                    logger.error(f"Failed to load tools after {max_retries} retries: {e.message}")
                    raise ToolError(
                        message=f"Failed to load tools after {max_retries} retries",
                        tool_name="load_tools",
                        severity=ErrorSeverity.ERROR,
                        original_exception=last_exception
                    )
            except Exception as e:
                # Convert other exceptions to ToolError
                last_exception = ToolError(
                    message=f"Error loading tools: {str(e)}",
                    tool_name="load_tools",
                    severity=ErrorSeverity.WARNING,
                    original_exception=e
                )
                retry_count += 1
                
                if retry_count <= max_retries:
                    # Calculate backoff delay with jitter to avoid thundering herd problem
                    delay = min(max_delay, base_delay * (2 ** (retry_count - 1)))
                    jitter = random.uniform(0, 0.1 * delay)  # 10% jitter
                    actual_delay = delay + jitter
                    
                    logger.warning(f"Failed to load tools: {str(e)}. Retrying in {actual_delay:.2f} seconds...")
                    await asyncio.sleep(actual_delay)
                else:
                    logger.error(f"Failed to load tools after {max_retries} retries: {str(e)}")
                    raise ToolError(
                        message=f"Failed to load tools after {max_retries} retries",
                        tool_name="load_tools",
                        severity=ErrorSeverity.ERROR,
                        original_exception=last_exception
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
                model_name="gemini-2.0-flash-001",
                temperature=0,
                credentials=self.credentials,
                project=os.getenv("GOOGLE_PROJECT"),
                location=os.getenv("GOOGLE_REGION")
            )
            model = model.bind_tools(self.tools)

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

        try:
            async with self.mcpClient.session("networkagent") as session:
                self.tools = await load_mcp_tools(session)
                self.tools_by_name = {tool.name: tool for tool in self.tools}

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
                    except TestAgentError as e:
                        # If it's already a TestAgentError, just log and add to outputs
                        logger.error(f"Error executing tool {tool_name}: {e.message}")
                        outputs.append(
                            ToolMessage(
                                content=json.dumps({"error": e.message, "details": e.details}),
                                name=tool_name,
                                tool_call_id=tool_call["id"],
                            )
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
        except Exception as e:
            logger.error(f"Unexpected error in tool_node: {str(e)}", exc_info=True)
            raise TestAgentError(
                message=f"Unexpected error in tool execution: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )


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
