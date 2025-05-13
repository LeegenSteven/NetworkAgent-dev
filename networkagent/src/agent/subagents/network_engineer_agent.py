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
import operator
from datetime import datetime
from typing import TypedDict, Annotated, Tuple, List
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.graph import StateGraph, END, START
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from langgraph.types import interrupt
from agent.tools.engineering import *
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

class NetworkEngineerAgentState(TypedDict):
    objective: str
    context: Annotated[list[AnyMessage], add_messages]
    steps: List[str]
    past_steps: Annotated[List[Tuple], operator.add]
    response: str

class NetworkEngineerAgent:
    _instance = None

    @staticmethod
    def get_instance():
        if NetworkEngineerAgent._instance is None:
            NetworkEngineerAgent._instance = NetworkEngineerAgent()
        return NetworkEngineerAgent._instance

    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        self.credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE", "/networkagent.json"))[0]

        self.safe_tools=[getLocations, getServiceDefinitions, getServices]
        self.safe_tools_by_name = {tool.name: tool for tool in self.safe_tools}
        self.unsafe_tools=[createLocation, createService, deleteLocation, deleteService]
        self.unsafe_tools_by_name = {tool.name: tool for tool in self.unsafe_tools}
        self.all_tools=self.safe_tools+self.unsafe_tools
        self.all_tools_by_name = {tool.name: tool for tool in self.all_tools}

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

        self.networkAgentApp = workflow.compile()

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
            
            network_services = getServiceDefinitions(None)
            network_service_instances = getServices(None)
            network_locations = getLocations(None)

            prompt = ChatPromptTemplate(
                [
                    ("system", network_engineer_prompts.planner_prompt), 
                    ("placeholder", "{messages}")
                ]
            ).partial(current_time=datetime.now())

            model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
            model = model.bind_tools(self.unsafe_tools)
            model = model.with_structured_output(Plan)

            runnable = prompt | model
            steps = runnable.invoke({"messages":[HumanMessage(content=state['objective'])]+state['context'],
                                    "network_service_instances": network_service_instances,
                                    "network_service_descriptors": network_services, 
                                    "network_locations": network_locations
                                    })

            return steps

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

            if last_message.lower() == 'yes' or last_message.lower() == 'y':
                return "execute_step"
            elif last_message.lower() == 'no' or last_message.lower() == 'n':
                return "response_summary"
            else:
                return "build_plan"

        else:
            return "response_summary"

    async def execute_step(self, state: NetworkEngineerAgentState):
        """
        Execute one step in the plan

        Returns:
            - context: the result of running the step prompt with the model
        """
        logger.info("executing step")

        if 'steps' not in state:
            logger.info("no planned steps found")
            return

        steps = state["steps"]
        past_steps = state['past_steps']

        # Determine which steps are not complete
        completed_step_names = [step_tuple[0] for step_tuple in past_steps]
        incomplete_steps = [step for step in steps if step not in completed_step_names]
        
        # If there are no incomplete steps, return None
        if not incomplete_steps:
            logger.info("All steps have been completed")
            return {"context": HumanMessage(content="All steps have been completed")}
        
        # Get the next step to work on
        this_step = incomplete_steps[0]
        logger.info(f"Next step to work on: {this_step}")

        prompt = ChatPromptTemplate(
            [
                ("system", network_engineer_prompts.execute_step_prompt), 
                ("placeholder", "{messages}")
            ]
        )
        model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
        model = model.bind_tools(self.all_tools)

        runnable = prompt | model
        response = runnable.invoke({"messages":[HumanMessage(content=this_step)],"network_service_descriptors": getServiceDefinitions(None)})

        return {"context": response}

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
        for tool_call in state["context"][-1].tool_calls:
            tool_result = self.all_tools_by_name[tool_call["name"]].invoke(tool_call["args"])
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        
        # Determine which steps are not complete
        steps = state["steps"]
        past_steps = state['past_steps']
        completed_step_names = [step_tuple[0] for step_tuple in past_steps]
        incomplete_steps = [step for step in steps if step not in completed_step_names]
        
        # Get the next step to work on (which is the one we just executed)
        if incomplete_steps:
            run_step = incomplete_steps[0]
        else:
            # If there are no incomplete steps, use the last step in the steps list
            run_step = steps[-1] if steps else "Unknown step"
        
        logger.info(f"Completed step: {run_step}")
        
        return {
            "past_steps": [(run_step, tool_result)],
            "context": outputs
        }

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