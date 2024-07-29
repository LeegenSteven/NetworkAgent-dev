from langchain_google_vertexai import ChatVertexAI
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdCallbackHandler
from langgraph import StateGraph, END
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.prebuilt import ToolNode
from agent.tools import *
import logging

logger = logging.getLogger(__name__)


class NetworkAgentState(TypedDict):
    messages: Annotated[list, add_messages]

class NetworkAgent:
    def __init__(self):

        safe_tools=[getServiceInfo]
        unsafe_tools=[]
        tools = safe_tools+unsafe_tools

        llm = ChatVertexAI(model_name="gemini-1.5-pro-preview-0409", 
                           temperature=0,
                           credentials="",
                           project="",
                           callback_manager=CallbackManager([StreamingStdCallbackHandler()]))

        network_agent_prompt=ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
                    You are a network engineering assistant. 

                    """
                )
            ]
        )

        self.network_agent_runnable = network_agent_prompt | llm.bind_tools(
            tools
        )

        self.toolNode = ToolNode(tools)

        networkGraph = StateGraph(NetworkAgentState)
        networkGraph.add_node("agent", self.call_model)
        networkGraph.add_node("tools", self.toolNode)
        networkGraph.set_entry_point("agent")
        networkGraph.add_conditional_edges(
            "agent",
            self.should_continue
        )
        self.networkAgentApp = networkGraph.compile(
            interrupt_before=[
                "unsafe_tools"
            ]
        )

    def should_continue(self, state: NetworkAgentState) -> Literal["__end__", "tools"]:
        messages = state["messages"]
        last_message = messages[-1]
        if not last_message.tool_calls:
            return END
        else:
            return "tools"
    
    async def call_model(self, state: NetworkAgentState, config: RunnableConfig):
        result = await self.network_agent_runnable.ainvoke(state, config)
        while True:
            if not result.tool_calls and (
                not result.content
                or isinstance(result.content, list)
                and not result.content[0].get("text")
            ):
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        return {"messages": result}

    async def run(self, question):
        logger.info("running network agent with question %s", question)
        config = {"configurable":{"thread_id": "1"}}
        inputs = {"messages": [HumanMessage(content=question)]}
        await self.networkAgentApp.ainvoke(inputs, config)