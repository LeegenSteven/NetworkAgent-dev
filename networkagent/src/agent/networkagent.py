from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langchain_google_vertexai import HarmBlockThreshold, HarmCategory
from langchain.callbacks.manager import CallbackManager
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Literal, cast
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.prebuilt import ToolNode
from langchain_core.tools import BaseTool, InjectedToolArg
from langchain_core.tools import tool as create_tool
from agent.tools import *
import google.auth
import logging
import json
from utils.tool_helpers import create_tool_node_with_fallback
import os

logger = logging.getLogger(__name__)

class NetworkAgentState(TypedDict):
    messages: Annotated[list, add_messages]

class NetworkAgent:
    def __init__(self):
        logger.info("loading networkagent credentials from path = %s", os.getcwd())

        # agent memory
        memory = MemorySaver()

        credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE", "/networkagent.json"))[0]

        safe_tools=[getCustomerLocations, getCustomerApplications, getServiceDefinitions, getServices]#, getServicePerformanceMetrics]
        unsafe_tools=[createService]#, deleteService, createTest, deleteTest]
        tools = safe_tools+unsafe_tools

        # build tools map for custom tool node
        self.tools_map ={}
        for tool_ in tools:
            logger.debug(tool_)
            if not isinstance(tool_, BaseTool):
                tool_ = cast(BaseTool, create_tool(tool_))
            self.tools_map[tool_.name]=tool_

        safety_settings = {
            HarmCategory.HARM_CATEGORY_UNSPECIFIED: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }
        llm = ChatVertexAI(model_name="gemini-1.5-flash-001", 
                           temperature=0,
                           credentials=credentials,
                           request_parallelism=1,
                           max_tokens=None,
                           max_retries=6,
                           stop=None,
                           safety_settings=safety_settings,
                           project=os.getenv("GOOGLE_PROJECT"),
                           location=os.getenv("GOOGLE_REGION"),
                           callback_manager=CallbackManager([StreamingStdOutCallbackHandler()]))

        network_agent_prompt=ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
                    You are a networking engineer specialist helper bot.
                    You job is to communicate with the user to help them manage their network connectivity services. 
                    You can help the user fulfill tasks such as:
                    - understanding which network connectivity services are available to use
                    - understand which networking services are deployed already for a particular customer
                    - understand which networking locations are available for a particular customer
                    - deploy new network connectivity services
                    - delete existing network connectivity services
                    - create and delete network connectivity tests 
                    - understand performance metrics for deployed connectivity service

                    Greet the users and ask how you can help them today.
                    - If necessary, seek clarifying details on what their request is.
                    - If the request involoves any of the following, use the tools provided to help the user with their task:
                        - get a list of available networking services providing a summary description of each and the data needed to instantiate them
                        - get a set of network locations based on a customer
                        - get the currently deployed networking services based on a customer
                        - Delete an existing networking service for a customer
                        - Create an existing networking service for a customer
                        - Create an connectivity test for a customer IT application
                        - Delete an existing connectivity test for a customer IT application
                        - Get the performance metrics for a deployed connectivity service
                        - The networking orchestrator itself uses Kubernetes CRDs to control the networking operations state. Hence the responses from this Tool will be in a kubernetes CRD format.
                    - If the request involves anything else, try your best to answer, but explain that you are an agent to be used specfically for managing network connectivity services and you should then list the capabilities above

                    """
                ),
                ("placeholder", "{messages}"),
            ]
        )

        self.network_agent_runnable = network_agent_prompt | llm.bind_tools(
            tools
        )

        networkGraph = StateGraph(NetworkAgentState)
        networkGraph.add_node("agent", self.call_model)
        networkGraph.add_node("tools", create_tool_node_with_fallback(tools))
        networkGraph.set_entry_point("agent")
        networkGraph.add_conditional_edges(
            "agent",
            self.should_continue
        )

        self.networkAgentApp = networkGraph.compile(
            checkpointer=memory,
            # interrupt_before=[
            #     "unsafe_tools"
            # ]
        )

    def should_continue(self, state: NetworkAgentState) -> Literal["__end__", "tools"]:
        logger.debug(state)
        messages = state["messages"]

        last_message = messages[-1]
        if not last_message.tool_calls:
            return END
        else:
            return "tools"
    
    def call_model(self, state: NetworkAgentState, config: RunnableConfig):
        logger.debug("calling model")
        try:
            logger.debug("<MESSAGES>")
            for m in state['messages']:
                if isinstance(m, AIMessage):
                    logger.debug(f"AIMESSAGE")
                elif isinstance(m, HumanMessage):
                    logger.debug(f"HUMAN")
                elif isinstance(m, ToolMessage):
                    logger.debug(f"TOOL")
                else:
                    logger.debug(m)

            while True:
                # Append to state
                state = {**state}
                # Invoke the tool-calling LLM
                result = self.network_agent_runnable.invoke(state, config)
                # If it is a tool call -> response is valid
                # If it has meaningful text -> response is valid
                # Otherwise, we re-prompt it b/c response is not meaningful
                if not result.tool_calls and (
                    not result.content
                    or isinstance(result.content, list)
                    and not result.content[0].get("text")
                ):
                    messages = state["messages"] + [("user", "Respond with a real output.")]
                    state = {**state, "messages": messages}
                    logger.debug("GOT DODGY ANSWER FROM GEMINI")
                else:
                    break

            logger.debug(result)
            return {"messages": result}
        
        except Exception as e:
            logger.debug("Caught Exception")
            logger.debug(e)

    def call_tool(self, state: NetworkAgentState):
        messages = state["messages"]
        last_message = messages[-1]
        logger.debug("+++++++++++++++++++++++++    TOOL CALL  +++++++++++++++++++++++++++++++++")
        logger.debug(last_message)
        output_messages = []
        for tool_call in last_message.tool_calls:
            try:
                logger.debug(tool_call)
                tool_result = self.tools_map[tool_call["name"]].invoke(tool_call["args"])
                output_messages.append(
                    ToolMessage(
                        content=tool_result,
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                    )
                )
            except Exception as e:
                # Return the error if the tool call fails
                logger.debug('Error')
                output_messages.append(
                    ToolMessage(
                        content="",
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                        additional_kwargs={"error": e},
                    )
                )
        logger.debug(output_messages)
        logger.debug("+++++++++++++++++++++++++    TOOL OUTPUT  +++++++++++++++++++++++++++++++++")
        return {"messages": output_messages}


    def run(self, question):
        logger.info("running network agent with question %s", question)
        config = {"configurable":{"thread_id": "1"}}
        inputs = {"messages": [HumanMessage(content=question)]}
        response = self.networkAgentApp.invoke(inputs, config)
        logger.info(response)
        return response['messages'][-1].content