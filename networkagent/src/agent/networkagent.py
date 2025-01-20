from langchain_google_vertexai.chat_models import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langchain_google_vertexai import HarmBlockThreshold, HarmCategory
from langchain.callbacks.manager import CallbackManager
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Sequence, Literal, cast
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, BaseMessage, trim_messages
from langchain_core.runnables.config import RunnableConfig
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langgraph.prebuilt import ToolNode, tools_condition
from agent.tools import *
from agent.rag_tools import *
import google.auth
import logging
import os

logger = logging.getLogger(__name__)

# Debug the Langchain flow of operations if logger is at DEBUG level
if logger.getEffectiveLevel() == logging.DEBUG:
  from langchain.globals import set_debug, set_verbose
  set_debug(True)
  set_verbose(False)


"""class NetworkAgentState(TypedDict):
    messages: Annotated[list, add_messages]"""

class NetworkAgentState(TypedDict):
    # The add_messages function defines how an update should be processed
    # Default is to replace. add_messages says "append"
    messages: Annotated[Sequence[BaseMessage], add_messages] # history of human and AI messages
    question: str # the last question asked
    context: str  # Context to answer the question from retrieved documents
    response: str # last generated response.

class NetworkAgent:
    def __init__(self):
        logger.debug("loading networkagent credentials from path = %s", os.getcwd())

        # agent memory
        memory = MemorySaver()

        credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE", "/networkagent.json"))[0]

        safe_tools=[getCustomerLocations, getCustomerApplications, getServiceDefinitions, getServices]#, getServicePerformanceMetrics]
        unsafe_tools=[createService, deleteService] #, createTest, deleteTest]
        #rag_tools=[retrieve_resources]
        agent_tools = safe_tools+unsafe_tools #+rag_tools

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
            You job is to communicate with the user to help them manage their network connectivity services
            and assess the state of the network resources in use. 
            You can help the user fulfill tasks such as:
            - understanding which network connectivity services are available to use
            - understand which network connectivity services are deployed already for a particular customer
            - understand which networking locations are available for a particular customer
            - deploy new network connectivity services
            - delete existing network connectivity services
            - create and delete network connectivity tests 
            - understand performance metrics for deployed connectivity service
            - understand the state of the network resources deployed (net, subnet, routes...) and their configuration

            Greet the users and ask how you can help them today.
            - If necessary, seek clarifying details on what their request is.
            - Connectivity services and networking services are synonyms
            - If the request involves any of the following, use the tools provided to help the user with their task:
                - get a list of available networking services providing a summary description of each and the data needed to instantiate them
                - get a set of network locations based on a customer
                - get the currently deployed networking services based on a customer
                - Delete an existing networking service for a customer
                - Create an existing networking service for a customer
                - Create an connectivity test for a customer IT application
                - Delete an existing connectivity test for a customer IT application
                - Get the performance metrics for a deployed connectivity service

            - If the request is about network resources such as network, subnetwork, routes, firewalls, VMs... 
              and their attributes like kind, name, status, parent node (also known as OwnerReference), network flow 
              connections (also know as network or subnetwork reference), creation time,... then use the relevant resource 
              descriptions provided in the Context below to answer. The resource descriptions provided in the context 
              are formatted as JSON strings.

            - If you still cannot answer explain that you are an agent to be used specifically 
              for managing network connectivity services and you should then list the capabilities above

            Context: {context}

            Conversation history: {messages}
            """

        network_agent_prompt=ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("placeholder", "{messages}"),
                ("human", "{question}")
            ]
        )

        retrieval_tool = ResourceRetrievalTool()

        def format_docs(docs):
            logger.debug(f"--- DOCS FOUND: {len(docs)} ")
            return "\n\n".join(doc.page_content for doc in docs)
        
        # Only keep the mast N messages in the history. Keeping
        # too many messages slows down Gemini as the RAG documents
        # stored in the context can be quite lengthy
        # FIXME: could not find a way to insert this trimmer in the 
        # runnable chain as it only understqnd a simple list of
        # not structured input as used below (context, question, messages)
        trimmer = trim_messages(
            token_counter=len,
            strategy="last",
            max_tokens=6,
            include_system=True,
        )

        self.network_agent_runnable = (
            {"context": retrieval_tool | format_docs, 
             "question": RunnablePassthrough(),
             "messages": RunnableLambda(lambda x: x["messages"])
            }
            | network_agent_prompt
            | self.model_with_tools
        )
        
        # Workflow nodes
        workflow = StateGraph(NetworkAgentState)
        workflow.add_node("clear_history", self.clear_history)
        workflow.add_node("agent_model", self.agent_model)
        workflow.add_node("agent_tools", ToolNode(agent_tools))
        # Workflow edges
        workflow.add_edge("clear_history", END)
        workflow.add_conditional_edges(
            "agent_model",
            # Assess agent decision
            tools_condition,
            {
                # Translate the condition outputs to nodes in our graph
                "tools": "agent_tools",
                END: END,
            },
        )
        workflow.add_conditional_edges(
            START, 
            self.should_clear_history,
            { "clear_history": "clear_history",
              "agent_model": "agent_model"})

        self.networkAgentApp = workflow.compile(
            checkpointer=memory,
            # interrupt_before=[
            #     "unsafe_tools"
            # ]
        )

        # App config (see run function below)
        self.config = {"configurable":{"thread_id": "1"}}

    
    def should_clear_history(self, state: NetworkAgentState) -> str:
        if state["question"].lower() in ['/reset', '/clear']: 
            return "clear_history" 
        else: 
            return "agent_model"

    # ---------------------
    # Node functions
    # ---------------------
    
    def clear_history(self, state: NetworkAgentState):
        """
        Reset the messages history.
        """
        logger.debug("---CLEAR HISTORY---")
        question = state["question"]
        if question in ['/clear', '/reset']:
            logger.debug("Chat reset requested")
            return {"messages": []}
        else:
            return state
            
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
        logger.debug("---MODEL REPONSE---")
        logger.debug(f"response: {response}")
        # We return just the response, because this will get 
        # added to the history of messages (see NetworkAgentState
        # definition above)
        state["messages"] = response

        return state
    
    def response_source(self,response):
        if isinstance(response, ToolMessage):
            response_source = "Tool API"
        elif isinstance(response, AIMessage):
            response_source = "Gemini"
        else:
            response_source = ""
        return response_source
    
    # ---------------------
    # Invoke agent app
    # ---------------------

    def run(self, question):
        logger.info("running network agent with question '%s'", question)
        inputs = {"messages": [HumanMessage(content=question)],
                  "question": question}
        responses = self.networkAgentApp.invoke(inputs, self.config)
        last_response = responses['messages'][-1]
        source = self.response_source(last_response)

        last_response_content = last_response.content
        logger.debug(f"Agent full response : {responses}")
        logger.info(f"Agent last response : {last_response_content}")

        return last_response_content,source