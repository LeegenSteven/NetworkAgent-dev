import logging
import gradio as gr
from agent.networkagent import NetworkAgent
import requests
import json
from langchain_community.agent_toolkits.openapi.spec import reduce_openapi_spec
from langchain_google_vertexai.chat_models import ChatVertexAI
from langchain.callbacks.manager import CallbackManager
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.schema import AIMessage, HumanMessage
import google.auth
from langchain.requests import RequestsWrapper
from langchain_community.agent_toolkits.openapi import planner
import os
import sys
from langchain_core.prompts import ChatPromptTemplate
from datetime import datetime

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

assitant_runnable = None
agent=None

def do_vertex():
    global assistant_runnable
    global agent
    assistant_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                You are a helpful network assistant
                
                Current time: {time}.
                """
            ),
            ("placeholder", "{messages}")
        ]
    ).partial(time=datetime.now())


    credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE", "/networkagent.json"))[0]
    logger.info(credentials)

    llm = ChatVertexAI(model_name="gemini-1.5-pro-001",
                        temperature=0,
                        credentials=credentials,
                        max_tokens=None,
                        max_retries=2,
                        stop=None,
                        project=os.getenv("GOOGLE_PROJECT"),
                        location=os.getenv("GOOGLE_REGION"),
                        callback_manager=CallbackManager([StreamingStdOutCallbackHandler()]))

    assistant_runnable = assistant_prompt | llm

    url = os.getenv("NETWORK_TOOLS_URL","http://networktools-lb-service:8080")+"/ui/openapi.json"
    logger.info("url = %s", url)
    response = requests.get(url)

    logger.info(json.dumps(response.json(),indent=4))
    api_spec = reduce_openapi_spec(response.json())
    logger.info(api_spec)

    requests_wrapper = RequestsWrapper()

    agent = planner.create_openapi_agent(
        api_spec,
        requests_wrapper,
        llm,
        allow_dangerous_requests=True,
    )

def agent_interaction(message, history):
    logger.info("new interaction %s", message)
    history_langchain_format = []

    for human, ai in history:
        history_langchain_format.append(HumanMessage(content=human))
        history_langchain_format.append(AIMessage(content=ai))

    history_langchain_format.append(HumanMessage(content=message))

    gpt_response = agent.invoke(message)
    logger.info(gpt_response)

    return gpt_response.content

if __name__ == '__main__':
    logger.info("starting Network Agent")

    if os.getenv("GOOGLE_REGION") is None or os.getenv("GOOGLE_ZONE") is None or os.getenv("GOOGLE_PROJECT") is None:
        logger.error("You must set GOOGLE_REGION/GOOGLE_ZONE/GOOGLEPROJECT environment variables")
        sys.exit(0)

    do_vertex()

    gr.ChatInterface(agent_interaction).launch()

