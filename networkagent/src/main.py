import logging
import streamlit as st
import utils.st_extension as st_ext
from streamlit.components.v1 import html
from streamlit_autorefresh import st_autorefresh

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

count = st_autorefresh(interval=2000, key="counter")

agentbuilder = """
<div id="messenger">
  <link rel="stylesheet" href="https://www.gstatic.com/dialogflow-console/fast/df-messenger/prod/v1/themes/df-messenger-default.css">
  <script src="https://www.gstatic.com/dialogflow-console/fast/df-messenger/prod/v1/df-messenger.js"></script>
  <df-messenger
    location="us-central1"
    project-id="free5gc-384814"
    agent-id="411228d8-5b52-474d-8e41-46ef2de67de6"
    language-code="en"
    max-query-length="-1">
    <df-messenger-chat
    chat-title="BT Network Agent">
    </df-messenger-chat>
  </df-messenger>
  <style>
    df-messenger {
      height: 100%;
      width: 100%;
      position: fixed;
      bottom: 1px;
      --df-messenger-font-color: #000;
      --df-messenger-font-family: Google Sans;
      --df-messenger-chat-background: #f3f6fc;
      --df-messenger-message-user-background: #d3e3fd;
      --df-messenger-message-bot-background: #fff;
    }
  </style>
</div>
"""

import graph.topology as topology

# Create columns with equal width
agentcolumn, graphcolumn, detailscolumn = st.columns([1,1,1]) 

# Agent Column
with agentcolumn:
  html(html=agentbuilder, height=700)
        
# Graph Column
with graphcolumn:
    graphcontainer = st.container(height=700, border=True)
    with graphcontainer:
        tab_labels = ["Net Topology", "Net resources", "Combined View"]
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