import os
import logging
import gradio as gr
from agent.networkagent import NetworkAgent

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)


async def agent_interaction(prompt, messages):
    logger.info("new interaction %s", prompt)

if __name__ == '__main__':
    logger.info("starting Network Agent")

    os.environ.get('KUBECONFIG')
    agent = NetworkAgent()
    gr.ChatInterface(agent_interaction).launch()

