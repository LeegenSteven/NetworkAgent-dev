import logging
import os
import kubernetes
from utils.k8s import get_client, get_credentials

import streamlit as st
# set_page_config must be executed before any other streamlit code.
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

import utils.st_extension as st_ext
from streamlit.components.v1 import html
#from streamlit_autorefresh import st_autorefresh


from langchain_core.messages import AIMessage, HumanMessage
from agent.networkagent import NetworkAgent
import graph.topology as topology

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

# Setup Agent and chat history
if "agent" not in st.session_state:
  st.session_state.agent = NetworkAgent()
if "chat_history" not in st.session_state:
  st.session_state.chat_history = [
      AIMessage(content="Hello, I am your network assistant. How can I help you ?"),
  ]

st.title("Autonomous Network Agent")
#count = st_autorefresh(interval=5000, key="counter")

# Create columns with equal width
agentcolumn, graphcolumn, detailscolumn = st.columns([1,1,1]) 

# Agent Column
with agentcolumn:
  chatcontainer=st.container(height=645, border=True)
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

    with chatcontainer.chat_message("Human"):
        st.markdown(prompt)

    with chatcontainer.chat_message("AI"):
      response=st.session_state.agent.run(prompt)
      st.markdown(response)
    
    st.session_state.chat_history.append(AIMessage(content=response))

# Graph Column
with graphcolumn:
  graphcontainer = st.container(height=700, border=True)
  with graphcontainer:
      tab_labels = ["Net Topology", "Net Resources", "Combined View"]
      selected_tab = st_ext.segmented_control(tab_labels, default=tab_labels[0], max_size=4, key="graph_tabs")

      if selected_tab == tab_labels[0]:
          selected_elts = topology.create_network_cytoscape()
      elif selected_tab == tab_labels[1]:
          selected_elts = topology.create_resource_cytoscape()
      elif selected_tab == tab_labels[2]:
          selected_elts = topology.create_combined_cytoscape()

# Details Column
with detailscolumn:
  detailscontainer = st.container(height=700, border=True)
  with detailscontainer:
    graph_info = topology.display_selected_elements(selected_elts)
    st.markdown(graph_info)

# Some useful Links at the foot of the page
project = os.environ.get("GOOGLE_PROJECT")
st.markdown(f"""**Some useful links**
  * [Spanner Graph database](https://console.cloud.google.com/spanner/instances/networktopology-instance/databases/networktopology-db/details/tables?invt=Abiyrw&project={project})
  * [Cluster Config status](https://console.cloud.google.com/kubernetes/config_management/packages?project={project})"""
    )
