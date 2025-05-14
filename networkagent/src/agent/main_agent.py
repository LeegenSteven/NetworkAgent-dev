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
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage, AIMessage
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, END, START
from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langgraph.types import Command
from agent.subagents import NetworkEngineerAgent, IncidentAgent
from agent.tools.engineering import getLocations, getServiceDefinitions, getServices, runTest, getRunningTests
import agent.prompts.main as main_prompts
from utils.k8s import get_credentials
import os
import datetime
import logging
import json

logger = logging.getLogger(__name__)

@tool
def transfer_to_network_engineer_agent():
    """Ask network engineer agent for help to create plans for new network services and network locations, or to create new network services and network locations."""
    return

@tool
def transfer_to_incident_agent():
    """Ask incident agent for help to run network tests and investigating logs or network service problems/issues."""
    return

class MainAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

class MainAgent():
    _instance = None
    session_id = 1

    @staticmethod
    def get_instance():
        if MainAgent._instance is None:
            MainAgent._instance = MainAgent()
        return MainAgent._instance

    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        self.current_agent = "main_agent"

        memory = MemorySaver()
        self.credentials = get_credentials()

        self.incident_agent=IncidentAgent.get_instance()
        self.network_engineer_agent=NetworkEngineerAgent.get_instance()

        # group tools by those to report their output and those we want directly report back to the user
        self.tools_not_report=[getServices, getLocations, getServiceDefinitions]
        self.tools_to_report=[transfer_to_incident_agent, transfer_to_network_engineer_agent]
        self.tools_to_report_by_name = {tool.name: tool for tool in self.tools_to_report}
        self.tools = self.tools_not_report+self.tools_to_report
        self.tools_by_name = {tool.name: tool for tool in self.tools}

        workflow = StateGraph(MainAgentState)
        workflow.add_node("call_model", self.call_model)
        workflow.add_node("tool_node", self.tool_node)
        workflow.add_node("engineer_node", self.engineer_node)
        workflow.add_node("incident_node", self.incident_agent.incidentAgentApp)
        workflow.add_edge(START, "call_model")
        workflow.add_conditional_edges("call_model", self.should_run_tool, ["tool_node", END])

        self.mainAgentApp = workflow.compile(checkpointer=memory)

    async def call_model(self, state: MainAgentState):
        logger.info("calling model")

        prompt = ChatPromptTemplate(
            [
                ("system", main_prompts.main_prompt), 
                ("placeholder", "{messages}")
            ]
        )

        model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
        model = model.bind_tools(self.tools)

        runnable = prompt | model
        response = runnable.invoke({"messages":state['messages']})

        return {'messages': response}

    async def engineer_node(self, state: MainAgentState):
        """
        Kick off an engineer flow. Map the last Human Message to an agent input
        """
        logger.info("running engineer flow")

        objective = None
        for message in reversed(state['messages']):
            if isinstance(message, HumanMessage):
                objective = message.content
                # check if the message is yes/y then likely we need to get the previous human message
                if objective.lower() != 'yes' or objective.lower()!='y':
                    break

        response=await self.network_engineer_agent.networkAgentApp.ainvoke({"objective": objective})

        # reset the agent
        self.current_agent="main_agent"

        return {"messages": AIMessage(content=f"{response['response']} \n\n returning to main agent")}


    async def incident_node(self, state: MainAgentState):
        """
        Kick off an incident agent flow. Map the last Human Message to agent input
        """
        logger.info("running incident flow")

        instruction = None
        for message in reversed(state['messages']):
            if isinstance(message, HumanMessage):
                instruction = message.content
                # check if the message is yes/y then likely we need to get the previous human message
                if instruction.lower() != 'yes' or instruction.lower()!='y':
                    break

        incident=await self.incident_agent.incidentAgentApp.ainvoke({"messages": [HumanMessage(content=instruction)]})

        # reset the agent
        self.current_agent="main_agent"

        return {"messages": incident['messages'][-1]}

    async def should_run_tool(self, state: MainAgentState):
        """
        Check if the last message was a tool message, and if so go to tool node, else 
        finish the flow
        """
        logger.info("should continue to tools or end")
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            logger.info("run tool")
            return "tool_node"

        logger.info("finish")
        return END

    async def tool_node(self, state: MainAgentState):
        """
        Call tool node. 
           - If a tool handoff a subagent is found then route to that agent's node in the graph
           - If a real tool call, call the tool
        """
        logger.info("calling tool")
        outputs = []
        for tool_call in state["messages"][-1].tool_calls:
            logger.info(tool_call)
            tool_call_id = tool_call["id"]
            tool_msg = {
                "role": "tool",
                "content": "Successfully transferred",
                "tool_call_id": tool_call_id,
            }
            if tool_call['name'] == "transfer_to_network_engineer_agent":
                logger.info("handing off to engineer")
                self.current_agent = "engineer_agent"
                return Command(goto="engineer_node", update={"messages": [ToolMessage(
                    content="Successfully transferred to Engineer",
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )]})
            elif tool_call['name'] == "transfer_to_incident_agent":
                logger.info("handing off to incident")
                self.current_agent = "incident_agent"
                return Command(goto="incident_node", update={"messages": [ToolMessage(
                    content="Successfully transferred to Incident",
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )]})

            # not a handoff so run the tool
            tool_result = self.tools_by_name[tool_call["name"]].invoke(tool_call["args"])
            return Command(goto="call_model", update={"messages": [
                        ToolMessage(
                            content=json.dumps(tool_result),
                            name=tool_call["name"],
                            tool_call_id=tool_call["id"],)
                        ]
                    })

    async def reset_conversation(self):
        """
        Increment the thread id and start a new memory session
        """
        logger.info("Reset conversation by incrementing the thread id %s", MainAgent.session_id)
        MainAgent.session_id=MainAgent.session_id+1
        # reset the current agent also - just in case we are in an error loop
        self.current_agent="main_agent"
        logger.info("thread is now %s", MainAgent.session_id)

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

    async def summarise_tool_call(self, tool_call):
        """
        Summarise a tool call - usually for validation from user to continue
        """
        logger.info("summarise the tool call")
        model = ChatVertexAI(model_name="gemini-2.0-flash-001",temperature=0,credentials=self.credentials,project=os.getenv("GOOGLE_PROJECT"),location=os.getenv("GOOGLE_REGION"))
        response = model.invoke([
            HumanMessage(content=f"""
                         summarise the tool call below

                         {tool_call}
                         """),
        ])
        logger.info(response)
        return response.content

    async def send_message(self, sio, sid, text):
        """
        Utility function to send a socket message back to the user
        """
        if text !='':
            response = {
                'id': f'response-{datetime.datetime.now().timestamp()}',
                'text': text,
                'source': "",
                'isUser': False,
                'timestamp': datetime.datetime.now().isoformat()
            }
            await sio.emit('chat_message', response, room=sid)

    async def check_for_plan_intterupt(self, sio, sid, chunk):
        """
        Given a chunk, check if there is an interrupt to confirm an engineer planning event
        Send the request for informtion to the user
        """
        # check if this is the initial interrupt to confirm a plan from the engineer agent
        # this chunk will appear multiple times so pick the chunk with empty tuple[0]
        if len(chunk[0]) ==0 and "__interrupt__" in chunk[1]:
            logger.info("interrupt from sub agents")

            if "__interrupt__" in chunk[1]:
                # if this is plan_confirmation then format the plan and the confirmation request to the user
                if 'plan_confirmation' in chunk[1]["__interrupt__"][0].value:
                    await self.send_message(sio,
                                            sid,
                                            await self.format_plan_confirmation_interrupt(
                                                chunk[1]["__interrupt__"][0].value['plan_confirmation'],
                                                chunk[1]["__interrupt__"][0].value['planned_steps'])
                                            )
                else:
                    # need to figure out how to get the key name to return to the user
                    await self.send_message(sio, 
                                            sid,
                                            chunk[1]["__interrupt__"][0].value)


    async def run(self, input, sio, sid):
        """
        Entry point to run a conversation between a user and agents. 
        """
        logger.info("input from user %s", input)

        config = {"configurable":{"thread_id": str(self.session_id)}}

        logger.info("current agent = %s", self.current_agent)

        # if the engineer agent is active then follow this path
        if self.current_agent == "engineer_agent":
            logger.info("Need confirmation response from user %s", input)
            async for chunk in self.mainAgentApp.astream(Command(resume=input), config, stream_mode="updates",subgraphs=True):
                if 'run_step_tool' in chunk[1]:                
                    # report status of complete steps
                    msg = f"__Running Step__ \n\n* {chunk[1]['run_step_tool']['past_steps'][0][0]}\n\nStatus: {chunk[1]['run_step_tool']['past_steps'][0][1]}"
                    await self.send_message(sio,sid, msg)

                if 'engineer_node' in chunk[1]:
                    # summarise objective
                    msg = chunk[1]['engineer_node']['messages']
                    await self.send_message(sio,sid,chunk[1]['engineer_node']['messages'].content)

                await self.check_for_plan_intterupt(sio, sid, chunk)
        else:
            # entry point into the main agent loop
            async for chunk in self.mainAgentApp.astream({"messages":[HumanMessage(content=input)]}, config=config, stream_mode="updates", subgraphs=True):
                if 'call_model' in chunk[1] and type(chunk[1]['call_model']['messages']) is AIMessage:
                    logger.info(chunk[1]['call_model'])
                    await self.send_message(sio, sid, chunk[1]['call_model']['messages'].content)

                if 'tool_node' in chunk[1]:                
                    # update to only send when its a tool allowed to report to the user
                    msg = chunk[1]['tool_node']['messages'][0]
                    if msg.name in self.tools_to_report_by_name:
                        await self.send_message(sio,sid,chunk[1]['tool_node']['messages'][0].content)

                if self.current_agent == "engineer_agent":
                    await self.check_for_plan_intterupt(sio, sid, chunk)



