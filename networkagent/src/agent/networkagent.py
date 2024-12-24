from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langchain_google_vertexai import HarmBlockThreshold, HarmCategory
from langchain.callbacks.manager import CallbackManager
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Literal, cast
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langgraph.prebuilt import ToolNode, tools_condition
from agent.tools import *
import google.auth
import logging
import os

# Uncomment lines below to debug the Langchain flow of operations
# from langchain.globals import set_debug, set_verbose
# set_debug(True)
# set_verbose(False)

logger = logging.getLogger(__name__)

class NetworkAgentState(TypedDict):
    messages: Annotated[list, add_messages]

class NetworkAgent:
    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        # agent memory
        memory = MemorySaver()

        credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE", "/networkagent.json"))[0]

        safe_tools=[getCustomerLocations, getCustomerApplications, getServiceDefinitions, getServices]#, getServicePerformanceMetrics]
        unsafe_tools=[createService, deleteService] #, createTest, deleteTest]
        agent_tools = safe_tools+unsafe_tools

        safety_settings = {
            HarmCategory.HARM_CATEGORY_UNSPECIFIED: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }
        self.model = ChatVertexAI(model_name="gemini-1.5-flash-002", 
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
        self.model_with_tools = self.model.bind_tools(agent_tools)

        system_prompt = """
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
            - If the request involves any of the following, use the tools provided to help the user with their task:
                - get a list of available networking services providing a summary description of each and the data needed to instantiate them
                - get a set of network locations based on a customer
                - get the currently deployed networking services based on a customer
                - Delete an existing networking service for a customer
                - Create an existing networking service for a customer
                - Create an connectivity test for a customer IT application
                - Delete an existing connectivity test for a customer IT application
                - Get the performance metrics for a deployed connectivity service
                - The networking orchestrator itself uses Kubernetes CRDs to control the networking operations state. Hence the responses from this Tool will be in a kubernetes CRD format.
            - if the request involves anything else, try your best to answer, but explain that you are an agent to be used specifically 
              for managing network connectivity services and you should then list the capabilities above
            """

        network_agent_prompt=ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("placeholder", "{messages}"),
            ]
        )

        self.network_agent_runnable = network_agent_prompt | self.model_with_tools
        
        networkAgentGraph = StateGraph(NetworkAgentState)
        networkAgentGraph.add_node("agent_model", self.agent_model)
        networkAgentGraph.add_node("agent_tools", ToolNode(agent_tools))
        networkAgentGraph.add_conditional_edges(
            "agent_model",
            # Assess agent decision
            tools_condition,
            {
                # Translate the condition outputs to nodes in our graph
                "tools": "agent_tools",
                END: END,
            },
            #self.should_continue
        )
        networkAgentGraph.add_edge(START, "agent_model")

        self.networkAgentApp = networkAgentGraph.compile(
            checkpointer=memory,
            # interrupt_before=[
            #     "unsafe_tools"
            # ]
        )
    
    def agent_model(self, state: NetworkAgentState, config: RunnableConfig):
        """
        Invokes the agent model to generate a response based on the current state. Given
        the question, it will decide to answer using the tools, or simply end.

        Args:
            state (messages): The current state

        Returns:
            dict: The updated state with the agent response appended to messages
        """
        logger.debug("---CALL AGENT---")
        logger.debug(f"state: {state}")

        response = self.network_agent_runnable.invoke(state)
        # We return a list, because this will get added to the existing list
        state["messages"] = response
        return state



    def run(self, question):
        logger.info("running network agent with question %s", question)
        config = {"configurable":{"thread_id": "1"}}
        inputs = {"messages": [HumanMessage(content=question)]}
        response = self.networkAgentApp.invoke(inputs, config)
        logger.info(response)
        return response['messages'][-1].content