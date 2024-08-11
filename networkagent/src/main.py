import logging
import gradio as gr
from agent.networkagent import NetworkAgent
from langchain_google_vertexai.chat_models import ChatVertexAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.callbacks.manager import CallbackManager
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.schema import AIMessage, HumanMessage
import google.auth
# from langchain.requests import RequestsWrapper
# from langchain_community.agent_toolkits.openapi import planner
import os
import sys
from langchain_core.prompts import ChatPromptTemplate
from datetime import datetime

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

assitant_runnable = None

def do_vertex():
    assistant_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                you are a helpful network assistant
                Current time: {time}.
                """
            ),
            ("placeholder", "{messages}")
        ]
    ).partial(time=datetime.now())

    global assistant_runnable

    credentials = google.auth.load_credentials_from_file("./networkagent.json")[0]
    logger.info(credentials)

    llm = ChatVertexAI(model_name="gemini-1.5-pro-001",
                        temperature=0,
                        credentials=credentials,
                        max_tokens=None,
                        max_retries=2,
                        stop=None,
                        project=os.getenv("PROJECT"),
                        location=os.getenv("REGION"),
                        callback_manager=CallbackManager([StreamingStdOutCallbackHandler()]))

    assistant_runnable = assistant_prompt | llm

    # import requests
    # url = os.getenv("NETWORKTOOLS_URL","http://networktools.automation:8080/ui/openapi.json")
    # response = requests.get(url)

    # logger.info(response.json())
    # requests_wrapper = RequestsWrapper()

    # agent = planner.create_openapi_agent(
    #     response.json(),
    #     requests_wrapper,
    #     llm
    # )

def agent_interaction(message, history):
    logger.info("new interaction %s", message)
    history_langchain_format = []

    for human, ai in history:
        history_langchain_format.append(HumanMessage(content=human))
        history_langchain_format.append(AIMessage(content=ai))

    history_langchain_format.append(HumanMessage(content=message))

    gpt_response = assistant_runnable.invoke({"messages": history_langchain_format})
    logger.info(gpt_response)

    return gpt_response.content

if __name__ == '__main__':
    logger.info("starting Network Agent")

    if os.getenv("REGION") is None or os.getenv("ZONE") is None or os.getenv("PROJECT") is None:
        logger.error("You must set REGION/ZONE/PROJECT environment variables")
        sys.exit(0)

    do_vertex()

    gr.ChatInterface(agent_interaction).launch()

