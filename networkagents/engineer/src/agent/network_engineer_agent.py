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

import logging
import os
import json
import operator
from datetime import datetime
from langgraph.checkpoint.memory import InMemorySaver
from collections.abc import AsyncIterable
from typing import TypedDict, Annotated, Tuple, List, Any
from pydantic import BaseModel, Field
from langchain_core.runnables.config import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph.message import add_messages
from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.graph import StateGraph, END, START
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage, AIMessage
from langgraph.types import interrupt, Command
from utils.k8s import get_credentials
from utils.error_handler import (
    EngineerAgentError,
    ToolError,
    PlanningError,
    ExecutionError,
    ErrorSeverity
)
import agent.prompts.network_engineer as network_engineer_prompts

logger = logging.getLogger(__name__)

if logger.getEffectiveLevel() == logging.DEBUG:
  from langchain.globals import set_debug, set_verbose
  set_debug(True)
  set_verbose(False)


class Plan(BaseModel):
    """Plan to follow in future"""
    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )

class PlanConfirmationResponse(BaseModel):
    """Whether the user has confirmed the plan proposed by the agent or not"""
    decision: str = Field(
        description="The user's decision to execute the proposed build plan, to cancel the plan or if they provided suggestions on how to update the plan: 'confirmed', 'cancelled', or 'amend'."
    )

class NetworkEngineerAgentState(TypedDict):
    objective: str
    context: Annotated[list[AnyMessage], add_messages]
    steps: List[str]
    past_steps: Annotated[List[Tuple], operator.add]
    response: str

class NetworkEngineerAgent:

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    _instance = None

    @classmethod
    async def get_instance(cls):
        if NetworkEngineerAgent._instance is None:
            NetworkEngineerAgent._instance = cls()
            await NetworkEngineerAgent._instance.load_tools()
        return NetworkEngineerAgent._instance

    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        self.credentials = get_credentials()

        self.network_service_definitions=None

        # limit the tools the agent can load
        self.allowed_tools=["getNetworkDesign", "getLocations", "createLocation", "deleteLocation", "getServiceDefinitions", "getServices", "createService", "deleteService"]
        # loaded tools
        self.tools=[]
        self.tools_by_name = {tool.name: tool for tool in self.tools}

        workflow = StateGraph(NetworkEngineerAgentState)
        workflow.add_node("build_plan", self.build_plan)
        workflow.add_node("confirm_plan", self.confirm_plan)
        workflow.add_node("execute_step", self.execute_step)
        workflow.add_node("run_step_tool", self.tool_node)
        workflow.add_node("response_summary", self.response_summary)
        workflow.add_edge(START, "build_plan")
        workflow.add_edge("build_plan", "confirm_plan")
        workflow.add_conditional_edges("confirm_plan", self.plan_complete_decision, ["execute_step", "build_plan", "response_summary"])
        workflow.add_conditional_edges("execute_step", self.should_run_tool, ["run_step_tool", "response_summary"])
        workflow.add_conditional_edges(
            "run_step_tool",
            self.should_end,
            ["execute_step", "response_summary"],
        )
        workflow.add_edge("response_summary", END)

        checkpointer = InMemorySaver()
        self.networkAgentApp = workflow.compile(checkpointer=checkpointer)

    async def load_tools(self):
        """
        Initialise the MCP tools with retry logic to ensure successful loading
        """
        logger.info("loading tools")
        agent_mcp_tool_address = os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080")

        self.mcpClient = MultiServerMCPClient(
            {
                "networkagent": {
                    "url": f"{agent_mcp_tool_address}/sse",
                    "transport": "sse",
                }
            }
        )
        try:
            self.tools = await self.mcpClient.get_tools()
            self.tools_by_name = {tool.name: tool for tool in self.tools}

            # cache definitions to speed things up - they dont change
            self.network_service_definitions = await self.tools_by_name["getServiceDefinitions"].ainvoke({})

            logger.info(f"Successfully loaded {len(self.tools)} tools")
            logger.debug(f"Loaded tools: {self.tools_by_name}")

        except Exception as e:
            raise ToolError(
                message=f"Error loading tools: {str(e)}",
                tool_name="load_tools",
                severity=ErrorSeverity.WARNING,
                original_exception=e
            )

    async def build_plan(self, state: NetworkEngineerAgentState, config):
        """
        Breaks an initial task request into the steps needed to execute the objective
        into an operational state

        Returns: 
            - steps: a list of executable prompts that can each execute a task that 
                     collectively deliver the users objective
        """

        logger.info("building the plan")

        if 'objective' in state:
            logger.info("objective %s", state['objective'])

            # run discovery tools
            network_design = ""
            network_services = ""
            network_service_instances = ""
            network_locations = ""
                    
            try:

                network_design = await self.tools_by_name["getNetworkDesign"].ainvoke({})
                # network_services = await self.tools_by_name["getServiceDefinitions"].ainvoke({})
                network_service_instances = await self.tools_by_name["getServices"].ainvoke({})
                network_locations = await self.tools_by_name["getLocations"].ainvoke({})

            except Exception as e:
                logger.warning(f"Error running tools: {str(e)}")
                raise ToolError(
                    message="Failed to run tool",
                    tool_name="discover crds and locations tools",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )

            prompt = ChatPromptTemplate(
                [
                    ("system", network_engineer_prompts.planner_prompt), 
                    ("placeholder", "{messages}")
                ]
            ).partial(current_time=datetime.now())

            try:
                model = ChatVertexAI(
                    model_name="gemini-2.0-flash-001",
                    temperature=0,
                    credentials=self.credentials,
                    project=os.getenv("GOOGLE_PROJECT"),
                    location=os.getenv("GOOGLE_REGION")
                )
                model = model.bind_tools(self.tools)
                model = model.with_structured_output(Plan)

                runnable = prompt | model
                steps = runnable.invoke({
                    "messages": [HumanMessage(content=state['objective'])] + state['context'],
                    "network_design": network_design,
                    "network_service_instances": network_service_instances,
                    "network_service_descriptors": self.network_service_definitions, 
                    "network_locations": network_locations
                })
                return steps
            except Exception as e:
                logger.error(f"Error in LLM planning: {str(e)}")
                raise PlanningError(
                    message="Failed to generate plan with LLM",
                    severity=ErrorSeverity.ERROR,
                    details={"objective": state.get('objective', 'Unknown')},
                    original_exception=e
                )


    async def confirm_plan(self, state: NetworkEngineerAgentState):
        """
        Ask the user to confirm the proposed planned steps

        If user says yes then goto execution
        If user says no then goto response_summary, otherwise add their comment to the context and replan

        Returns:
            - context: Human message with the users response
        """
        logger.info("confirm_plan node - confirming the plan with the user")

        if 'steps' in state:
            logger.info("sending interrupt")

            response = interrupt(
                {
                    "plan_confirmation": "You can amend this plan or execute by responding yes/no.",
                    "planned_steps": state["steps"]
                }
            )

            return {'context': HumanMessage(content=response)}

    async def plan_complete_decision(self, state: NetworkEngineerAgentState):
        """
        Conditional branch function to decide route after confirmation request. 
        If the user answers yes then move on to execute the plan, user answer is no then goto response_summary, 
        otherwise go back and replan based on their comments

        Returns:
            - Union["execute_step", "response_summary", "build_plan"]
        """
        logger.info("decide to re-plan or not based on human feed back")

        if 'steps' in state:
            last_message = state['context'][-1].content
            try:
                model = ChatVertexAI(
                    model_name="gemini-2.0-flash-001",
                    temperature=0,
                    credentials=self.credentials,
                    project=os.getenv("GOOGLE_PROJECT"),
                    location=os.getenv("GOOGLE_REGION")
                )
                model = model.with_structured_output(PlanConfirmationResponse)
                plan_decision = await model.ainvoke(last_message)
                logger.info(f"Planning decision from the user is {plan_decision}.")

                if plan_decision.decision == 'confirmed':
                    return 'execute_step'
                elif plan_decision.decision == 'cancelled':
                    return 'response_summary'
                elif plan_decision.decision == 'amend':
                    return 'build_plan'

                return 'response_summary'            
            except Exception as e:
                logger.error(f"Error in LLM planning: {str(e)}")
                raise PlanningError(
                    message="Failed to get users decision",
                    severity=ErrorSeverity.ERROR,
                    details={"objective": state.get('objective', 'Unknown')},
                    original_exception=e
                )
        else:
            return "response_summary"

    async def execute_step(self, state: NetworkEngineerAgentState):
        """
        Execute one step in the plan

        Returns:
            - context: the result of running the step prompt with the model
        """
        logger.info("executing step")

        try:

            if 'steps' not in state:
                logger.info("no planned steps found")
                raise ExecutionError(
                    message="No planned steps found for execution",
                    severity=ErrorSeverity.ERROR
                )

            steps = state["steps"]
            past_steps = state['past_steps']

            # Determine which steps are not complete
            completed_step_names = [step_tuple[0] for step_tuple in past_steps]
            incomplete_steps = [step for step in steps if step not in completed_step_names]
            
            # If there are no incomplete steps, return None
            if not incomplete_steps:
                logger.info("All steps have been completed")
                return {"context": AIMessage(content="All steps have been completed")}
            
            # Get the next step to work on
            this_step = incomplete_steps[0]
            logger.info(f"Next step to work on: {this_step}")

            try:
                prompt = ChatPromptTemplate(
                    [
                        ("system", network_engineer_prompts.execute_step_prompt), 
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
                response = runnable.invoke({
                    "messages": [HumanMessage(content=this_step)],
                    "network_service_descriptors": self.network_service_definitions
                })

                return {"context": response}

            except Exception as e:
                logger.error(f"Error executing step with LLM: {str(e)}")
                raise ExecutionError(
                    message=f"Failed to execute step with LLM: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    details={"step": this_step},
                    original_exception=e
                )

        except Exception as e:
            logger.error(f"Unexpected error in execute_step: {str(e)}")
            raise ExecutionError(
                message=f"Unexpected error in step execution: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )


    async def should_run_tool(self, state: NetworkEngineerAgentState):
        """
        Check if the model wants to run a tool after evaluating the step prompt. 
        If yes then goto run_step_tool, if not jump to the response_summary node. 

        Returns: 
            - Union["run_step_tool", "response_summary"]
        """
        logger.info("should continue to tools or end")

        context = state["context"]
        last_message = context[-1]
        if last_message.tool_calls:
            logger.info("run step tool")
            return "run_step_tool"

        logger.info("finish")
        return "response_summary"

    async def tool_node(self, state: NetworkEngineerAgentState):
        """
        Call the tool requested by the execution node and update the past_steps with 
        the tool execution response details. 

        Returns:
            - past_steps: tuple with step prompt and result from tool
            - context: Add a ToolMessage with tool details
        """
        logger.info("running the tool")
        outputs = []
        tool_result = {}
        
        try:
            for tool_call in state["context"][-1].tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                
                logger.info(f"calling tool {tool_name}")
                logger.info(json.dumps(tool_args, indent=3))
                
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
                    raise error
        except Exception as e:
            logger.error(f"Unexpected error in tool_node: {str(e)}")
            error = ExecutionError(
                message=f"Unexpected error in tool execution: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            
        # Determine which steps are not complete
        steps = state.get("steps", [])
        past_steps = state.get('past_steps', [])
        completed_step_names = [step_tuple[0] for step_tuple in past_steps]
        incomplete_steps = [step for step in steps if step not in completed_step_names]
        
        # Get the next step to work on (which is the one we just executed)
        if incomplete_steps:
            run_step = incomplete_steps[0]
        else:
            # If there are no incomplete steps, use the last step in the steps list
            run_step = steps[-1] if steps else "Unknown step"
        
        logger.info(f"Completed step: {run_step}")
        
        result = {
            "past_steps": [(run_step, tool_result)],
            "context": outputs
        }
        
        # If we caught an error earlier, re-raise it now that we've updated the state
        if 'error' in locals():
            raise error
            
        return result

    async def should_end(self, state: NetworkEngineerAgentState):
        """
        Conditional branch function that decides to continue looping over remaining steps or to complete
        the flow and goto response_summary

        Returns:
            - Union["execut_step", "response_summary"]
        """
        logger.info("deciding whether to finish")
        
        # Determine which steps are not complete
        steps = state["steps"]
        past_steps = state['past_steps']
        completed_step_names = [step_tuple[0] for step_tuple in past_steps]
        incomplete_steps = [step for step in steps if step not in completed_step_names]
        
        # If there are still incomplete steps, continue executing
        if incomplete_steps:
            logger.info(f"Incomplete steps remaining: {len(incomplete_steps)}")
            return "execute_step"
        else:
            logger.info("All steps have been completed")
            return "response_summary"


    async def response_summary(self, state: NetworkEngineerAgentState):
        """
        Summarise the execution of the whole plan. If no objective or no past steps, the user may have cancelleed 
        or give a dumb objective that didnt make sense.
        """
        logger.info("summarise plan execution")

        try:
            prompt = ChatPromptTemplate(
                [
                    ("system", network_engineer_prompts.summary_prompt), 
                    ("placeholder", "{messages}")
                ]
            )
            model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
            runnable = prompt | model

            # if no objective was given for some reason, there will be no steps
            steps = None
            if 'steps' in state:
                steps = state['steps']

            # past_steps may be None if the user said no
            past_steps = None
            if 'past_steps' in state:
                past_steps = state['past_steps']

            response = await runnable.ainvoke({"messages": [HumanMessage(content="keep the response concise")], 
                                      "steps": steps, 
                                      "past_steps": past_steps})


            return {'response': response.content}
        except Exception as e:
            logger.error(f"Error in response summary: {str(e)}")
            raise ExecutionError(
                message="Failed to generate response summary",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

    async def format_plan_confirmation_interrupt(self, confirmation, steps):
        """
        Convert the plan to markdown and ask for confirmation
        """
        plan_string = "The following steps are needed to achieve your objective\n\n"
        for s in steps:
            plan_string = plan_string + "* "+s+"\n"
        plan_string = plan_string + "\n\n"
        plan_string = plan_string + confirmation
        return plan_string

    async def check_for_plan_intterupt(self, chunk):
        """
        Given a chunk, check if there is an interrupt to confirm an engineer planning event

        Returns:
            interrupt: None if no interrupt, else interrupt dict
        """
        logger.info("Checking for interrupt")
        if "__interrupt__" in chunk:
            logger.info("interrupt caught")

            if "__interrupt__" in chunk:
                if 'plan_confirmation' in chunk["__interrupt__"][0].value:
                    return await self.format_plan_confirmation_interrupt(
                        chunk["__interrupt__"][0].value['plan_confirmation'],
                        chunk["__interrupt__"][0].value['planned_steps']
                    )
        return None

    async def parse_events(self, chunk):
        """
        Parse out interesting events to pass back to the A2A client
        """
        logger.info("PARSE ENGINEER EVENT")
        logger.info(chunk)

        try:
            # Check for errors in the chunk
            if isinstance(chunk, dict) and 'error' in chunk:
                error = chunk['error']
                if isinstance(error, EngineerAgentError):
                    return {
                        'is_task_complete': error.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL],
                        'require_user_input': False,
                        'error': error,
                        'content': f"[{error.severity.value}] {error.message}"
                    }
                else:
                    # Convert to EngineerAgentError if it's not already
                    converted_error = EngineerAgentError(
                        message=str(error),
                        severity=ErrorSeverity.ERROR,
                        original_exception=error if isinstance(error, Exception) else None
                    )
                    return {
                        'is_task_complete': True,
                        'require_user_input': False,
                        'error': converted_error,
                        'content': f"[ERROR] {str(error)}"
                    }

            # Check for interrupts
            interrupt = await self.check_for_plan_intterupt(chunk)
            if interrupt is not None:
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': interrupt,
                }

            if 'run_step_tool' in chunk:
                # Check if there's an error in the tool result
                if (isinstance(chunk['run_step_tool'], dict) and 
                    'past_steps' in chunk['run_step_tool'] and 
                    chunk['run_step_tool']['past_steps'] and 
                    isinstance(chunk['run_step_tool']['past_steps'][0][1], dict) and 
                    'error' in chunk['run_step_tool']['past_steps'][0][1]):
                    
                    error_data = chunk['run_step_tool']['past_steps'][0][1]['error']
                    error_msg = error_data if isinstance(error_data, str) else str(error_data)
                    error = ToolError(
                        message=f"Tool execution error: {error_msg}",
                        tool_name=chunk['run_step_tool']['past_steps'][0][0],
                        severity=ErrorSeverity.ERROR
                    )
                    return {
                        'is_task_complete': False,
                        'require_user_input': False,
                        'error': error,
                        'content': f"[ERROR] Tool execution failed: {error_msg}"
                    }
                
                # report status of complete steps
                msg = f"__Running Step__ \n\n* {chunk['run_step_tool']['past_steps'][0][0]}\n\nStatus: {chunk['run_step_tool']['past_steps'][0][1]}"
                return {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': msg,
                }

            if 'response_summary' in chunk:  
                # response summary is the final step so return all is complete
                msg = "__Response Summary__ \n\n"
                if 'response' in chunk['response_summary']:
                    msg += chunk['response_summary']['response']
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': msg,
                }

            return None
        except Exception as e:
            logger.error(f"Error parsing events: {str(e)}", exc_info=True)
            error = EngineerAgentError(
                message=f"Error parsing events: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            return {
                'is_task_complete': True,
                'require_user_input': False,
                'error': error,
                'content': f"[ERROR] Error parsing events: {str(e)}"
            }

    async def stream(self, query: str, sessionId: str) -> AsyncIterable[dict[str, Any]]:
        """
        Bridge between A2A stream and langgraph stream
        """
        inputs: dict[str, Any] = {'messages': [('user', query)]}
        config: RunnableConfig = {'configurable': {'thread_id': sessionId}}

        logger.info("langgraph stream started with %s and thread id %s", query, sessionId)

        try:
            state = self.networkAgentApp.get_state(config)
            
            if len(state.interrupts) > 0:
                try:
                    async for chunk in self.networkAgentApp.astream(Command(resume=query), config=config, stream_mode="updates"):
                        try:
                            event = await self.parse_events(chunk)
                            if event is not None:
                                logger.info("YIELDING EVENT")
                                logger.info(event)
                                yield event
                        except EngineerAgentError as e:
                            logger.error(f"Error in stream (interrupt mode) parse_events: {e.message}")
                            yield {
                                'is_task_complete': e.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL],
                                'require_user_input': False,
                                'error': e,
                                'content': f"[{e.severity.value}] {e.message}"
                            }
                        except Exception as e:
                            logger.error(f"Unexpected error in stream (interrupt mode): {str(e)}", exc_info=True)
                            error = EngineerAgentError(
                                message=f"Unexpected error in stream processing: {str(e)}",
                                severity=ErrorSeverity.ERROR,
                                original_exception=e
                            )
                            yield {
                                'is_task_complete': True,
                                'require_user_input': False,
                                'error': error,
                                'content': f"[ERROR] Unexpected error in stream processing: {str(e)}"
                            }
                except Exception as e:
                    logger.error(f"Error in stream (interrupt mode): {str(e)}", exc_info=True)
                    error = EngineerAgentError(
                        message=f"Error in stream processing (interrupt mode): {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                    yield {
                        'is_task_complete': True,
                        'require_user_input': False,
                        'error': error,
                        'content': f"[ERROR] Error in stream processing (interrupt mode): {str(e)}"
                    }
                return

            try:
                async for chunk in self.networkAgentApp.astream({"objective": query}, config=config, stream_mode="updates"):
                    try:
                        event = await self.parse_events(chunk)
                        if event is not None:
                            logger.info("YIELDING EVENT")
                            logger.info(event)
                            yield event
                    except EngineerAgentError as e:
                        logger.error(f"Error in stream parse_events: {e.message}")
                        yield {
                            'is_task_complete': e.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL],
                            'require_user_input': False,
                            'error': e,
                            'content': f"[{e.severity.value}] {e.message}"
                        }
                    except Exception as e:
                        logger.error(f"Unexpected error in stream: {str(e)}", exc_info=True)
                        error = EngineerAgentError(
                            message=f"Unexpected error in stream processing: {str(e)}",
                            severity=ErrorSeverity.ERROR,
                            original_exception=e
                        )
                        yield {
                            'is_task_complete': True,
                            'require_user_input': False,
                            'error': error,
                            'content': f"[ERROR] Unexpected error in stream processing: {str(e)}"
                        }
            except Exception as e:
                logger.error(f"Error in stream: {str(e)}", exc_info=True)
                error = EngineerAgentError(
                    message=f"Error in stream processing: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                yield {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'error': error,
                    'content': f"[ERROR] Error in stream processing: {str(e)}"
                }
        except Exception as e:
            logger.error(f"Critical error in stream: {str(e)}", exc_info=True)
            error = EngineerAgentError(
                message=f"Critical error in stream processing: {str(e)}",
                severity=ErrorSeverity.CRITICAL,
                original_exception=e
            )
            yield {
                'is_task_complete': True,
                'require_user_input': False,
                'error': error,
                'content': f"[CRITICAL] Critical error in stream processing: {str(e)}"
            }
