import os
import logging
import gradio as gr
from agent.networkagent import NetworkAgent
from langchain.schema import AIMessage, HumanMessage
import vertexai

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

    return gpt_response

if __name__ == '__main__':
    logger.info("starting Network Agent")

    # os.environ.get('KUBECONFIG')
    gr.ChatInterface(agent_interaction).launch()

