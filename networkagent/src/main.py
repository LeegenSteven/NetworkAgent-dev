import logging
#from agent.networkagent import NetworkAgent
#from langchain_core.messages import AIMessage, HumanMessage
import streamlit as st
from datetime import datetime
import utils.st_extension as st_ext
from streamlit.components.v1 import html

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
# st.markdown("""
#     <style>
#         .reportview-container {
#             margin-top: -2em;
#         }
#         #MainMenu {visibility: hidden;}
#         .stDeployButton {display:none;}
#         footer {visibility: hidden;}
#         #stDecoration {display:none;}
#     </style>
# """, unsafe_allow_html=True)

st.markdown(
    """
<style>
    iframe.stIFrame{
  height:670px !important;
  }
</style>
""",
    unsafe_allow_html=True,
)

agentbuilder="""<link rel="stylesheet" href="https://www.gstatic.com/dialogflow-console/fast/df-messenger/prod/v1/themes/df-messenger-default.css">
<script src="https://www.gstatic.com/dialogflow-console/fast/df-messenger/prod/v1/df-messenger.js"></script>
<df-messenger
  location="us-central1"
  project-id="free5gc-384814"
  agent-id="411228d8-5b52-474d-8e41-46ef2de67de6"
  language-code="en"
  max-query-length="-1">
  <df-messenger-chat-bubble
   chat-title="BT Network Agent">
  </df-messenger-chat-bubble>
</df-messenger>
<style>
  df-messenger {
    z-index: 999;
    position: fixed;
    --df-messenger-font-color: #000;
    --df-messenger-font-family: Google Sans;
    --df-messenger-chat-background: #f3f6fc;
    --df-messenger-message-user-background: #d3e3fd;
    --df-messenger-message-bot-background: #fff;
    bottom: 16px;
    right: 16px;
  }
</style>"""

import graph.topology as topology

# st.title("💬 Gemini Network Agent")

reset_chat = st.sidebar.button("Reset Chat")

# # Setup Agent and chat history
# if "agent" not in st.session_state:
#     st.session_state.agent = NetworkAgent()

# if "chat_history" not in st.session_state:
#     st.session_state.chat_history = [
#         # AIMessage(content="Hello, I am a network assistant. How can I help you?"),
#     ]

# Create columns
chatcolumn, graphcolumn, vais = st.columns(3)
chatcontainer = chatcolumn.container(height=700, border=True)

# with graphcolumn:
graphcontainer = graphcolumn.container(height=700, border=True)
graphelementcontainer = graphcolumn.container(height=300, border=True)

vaiscontainer = vais.container(height=700, border=True)


with graphcontainer:

    tab_labels = ["Network Topology", "K8s resources", "Combined View"]
    selected_tab = st_ext.segmented_control(tab_labels, default=tab_labels[0], max_size=4, key="graph_tabs")

    if selected_tab == tab_labels[0]:
        selected_elts = topology.create_network_cytoscape()
        #st.markdown("Content in A")
        #st.button("Go to B", on_click=lambda: st.session_state.update(tabs="B"))
    elif selected_tab == tab_labels[1]:
        selected_elts = topology.create_resource_cytoscape()
    elif selected_tab == tab_labels[2]:
        selected_elts = topology.create_combined_cytoscape()

with graphelementcontainer:
    graph_info = topology.display_selected_elements(selected_elts)
    st.markdown(graph_info)

# # Display chat messages from history on app rerun
# for message in st.session_state.chat_history:
#     if isinstance(message, AIMessage):
#         with chatcontainer.chat_message("AI"):
#             st.markdown(message.content)
#     elif isinstance(message, HumanMessage):
#         with chatcontainer.chat_message("Human"):
#             st.markdown(message.content)

# # Accept user input
# if prompt := st.chat_input("Type your message here...?"):
#     st.session_state.chat_history.append(HumanMessage(content=prompt))
#     # Display user message in chat message container
#     with chatcontainer.chat_message("Human"):
#         st.markdown(prompt)

#     with chatcontainer.chat_message("AI"):
#         response=st.session_state.agent.run(prompt)
#         logger.info(response)
#         st.markdown(response)
#     st.session_state.chat_history.append(AIMessage(content=response))

with vaiscontainer:
  html(agentbuilder)
