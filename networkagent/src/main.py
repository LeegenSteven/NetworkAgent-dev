import logging
from agent.networkagent import NetworkAgent
from langchain_core.messages import AIMessage, HumanMessage
import streamlit as st

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='DEBUG', format=log_format)
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
import graph.topology as topology

st.title("💬 Gemini Network Agent")

reset_chat = st.sidebar.button("Reset Chat")

# Setup Agent and chat history
if "agent" not in st.session_state:
    st.session_state.agent = NetworkAgent()

if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        # AIMessage(content="Hello, I am a network assistant. How can I help you?"),
    ]

# Create columns
chatcolumn, graphcolumn = st.columns(2, )
chatcontainer = chatcolumn.container(height=500, border=True)

with graphcolumn:
    graphcontainer=st.container(height=500, border=True)
with graphcontainer:
    st.markdown("<h5 style='text-align: center;'>Network Topology Overview</h1>", unsafe_allow_html=True)
    selected = topology.create_cytoscape()

# Display chat messages from history on app rerun
for message in st.session_state.chat_history:
    if isinstance(message, AIMessage):
        with chatcontainer.chat_message("AI"):
            st.markdown(message.content)
    elif isinstance(message, HumanMessage):
        with chatcontainer.chat_message("Human"):
            st.markdown(message.content)

# Accept user input
if prompt := st.chat_input("Type your message here...?"):
    st.session_state.chat_history.append(HumanMessage(content=prompt))
    # Display user message in chat message container
    with chatcontainer.chat_message("Human"):
        st.markdown(prompt)

    with chatcontainer.chat_message("AI"):
        response=st.session_state.agent.run(prompt)
        logger.info(response)
        st.markdown(response)
    st.session_state.chat_history.append(AIMessage(content=response))
