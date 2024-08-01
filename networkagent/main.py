import os
import logging
import gradio as gr
from agent.networkagent import NetworkAgent
from langchain.schema import AIMessage, HumanMessage
import vertexai
from google.auth import default

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

agent = NetworkAgent()

async def agent_interaction(message, history):
    logger.info("new interaction %s", message)
    history_langchain_format = []

    for human, ai in history:
        history_langchain_format.append(HumanMessage(content=human))
        history_langchain_format.append(AIMessage(content=ai))

    history_langchain_format.append(HumanMessage(content=message))

    gpt_response = await agent.run(message)
    logger.info(gpt_response)

    # return gpt_response.content

from google.auth.transport.requests import AuthorizedSession

credentials, _ = default()
session = AuthorizedSession(credentials=credentials)
import google.auth
def test_credentials():
  try:
    google.auth.default()
    print("Credentials are valid.")
  except Exception as e:
    print("Credentials are invalid:", e)

if __name__ == '__main__':
    logger.info("starting Network Agent")

    vertexai.init(project="free5gc-384814", location="europe-west2")



    # os.environ.get('KUBECONFIG')
    gr.ChatInterface(agent_interaction).launch()

