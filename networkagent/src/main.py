from datetime import datetime

import hmac
import logging
import os
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

import streamlit as st
# set_page_config must be executed before any other streamlit code.
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

import utils.st_extension as st_ext

from langchain_core.messages import AIMessage, HumanMessage
from agent.networkagent import NetworkAgent
import graph.topology as topology
import agent.logs as logs
from utils.gitea_extension import get_gitea_url

# From https://ploomber.io/blog/streamlit-password/
def check_login_password():
    # Returns `True` if the user had the correct password.
    def password_entered():
      # If you use .streamlit/secrets.toml, replace os.environ.get with st.secrets["STREAMLIT_PASSWORD"]
      if hmac.compare_digest(st.session_state["password"], os.environ.get("WEBAPPS_PWD", "")):
          st.session_state["password_correct"] = True
          del st.session_state["password"]
      else:
          st.session_state["password_correct"] = False

    # Returns `True` if the user had the correct login.
    def login_entered():
      # If you use .streamlit/secrets.toml, replace os.environ.get with st.secrets["STREAMLIT_PASSWORD"]
      if hmac.compare_digest(st.session_state["login"], os.environ.get("WEBAPPS_LOGIN", "")):
          st.session_state["login_correct"] = True
          del st.session_state["login"]
      else:
          st.session_state["login_correct"] = False

    # Return True if the login and passwords are correct.
    if st.session_state.get("password_correct", False) and st.session_state.get("login_correct", False):
        return True

    # Show input for password. Use a 25%
    # width column to limit size of input field
    col1, _ = st.columns([1,3])
    with col1:
      st.text_input("Login", type="default", on_change=login_entered, key="login")
      st.text_input("Password", type="password", on_change=password_entered, key="password")

      if "password_correct" in st.session_state:
          st.error("😕 Login or password incorrect")
      return False


def reset_chat_history():
  """
  Resets the chat history in the Streamlit session state.
  """
  st.session_state.chat_history = [
    AIMessage(content="Hello, I am your network assistant. How can I help you ?"),
  ]
  st.session_state.agent = NetworkAgent()

def init_graph_autorefresh():
  st.session_state.graph_autorefresh = True

# ---------------------------------
# Graph fragment autorefresh
# ---------------------------------
# Start with auto refresh disabled
if "graph_autorefresh" not in st.session_state:
  init_graph_autorefresh()
if st.session_state.graph_autorefresh:
  run_every = 3
else:
  run_every = None

# Graph fragment to refresh
@st.fragment(run_every=run_every)
def graphcontainer_fragment():
  tab_labels = ["Net Topology", "Net Resources", "Combined View"]
  selected_tab = st_ext.segmented_control(tab_labels, default=tab_labels[0], max_size=4, key="graph_tabs")

  if selected_tab == tab_labels[0]:
      selected_elts = topology.create_network_cytoscape()
  elif selected_tab == tab_labels[1]:
      selected_elts = topology.create_resource_cytoscape()
  elif selected_tab == tab_labels[2]:
      selected_elts = topology.create_combined_cytoscape()
  else:
     # should not happen but just in case
     selected_elts = { 'nodes': [] }

  on_off = "ON" if st.session_state.graph_autorefresh else "OFF"
  time_str = f"<p style='font-size:14px;'>Last updated: {datetime.now().replace(microsecond=0)} (Autorefresh: <b>{on_off})</b></p>"
  st.markdown(time_str, unsafe_allow_html=True)

  # The hack below is needed to run the agent again
  # when an element is selected on the graph so that the
  # property panel refreshes and display the selected node
  # properties. But we don't want to go
  # throuh the authentication process again
  st.session_state.fragment_rerun = True
  if "selected_elts" not in st.session_state or (st.session_state["selected_elts"] != selected_elts):
    st.session_state["selected_elts"] = selected_elts
    st.rerun()
  # I'm still returning this but it is of no use
  # in a fragment
  return selected_elts

# Log fragment to refresh (same refresh rate as graph)
@st.fragment(run_every=run_every)
def logcontainer_fragment():
  log_entries = logs.fetch_log_entries()
  st.text(logs.format_rows(log_entries))

def toggle_autorefresh():
  if "graph_autorefresh" not in st.session_state:
    init_graph_autorefresh()
  else:
    st.session_state.graph_autorefresh = not st.session_state.graph_autorefresh


# Setup Agent and chat history
if "agent" not in st.session_state:
  st.session_state.agent = NetworkAgent()
if "chat_history" not in st.session_state:
  reset_chat_history()

project = os.environ.get("GOOGLE_PROJECT")

st.title("Autonomous Network Agent")

# Don't go through the authentication process
# again if the rerun comes from the graph fragment
# (see comment in the fragment function)
if "fragment_rerun" not in st.session_state:
  if not check_login_password():
    st.stop()
else:
  del st.session_state["fragment_rerun"]
  st.session_state["login"] = os.environ.get("WEBAPPS_LOGIN", "")
  st.session_state["password"] = os.environ.get("WEBAPPS_PWD", "")

# --------------------------------------------
# Side Bar (Settings and links)
#---------------------------------------------

with st.sidebar:
  st.title("Settings")
  if st.button("Reset chat"):
    reset_chat_history()
  if st.toggle("Graph Autorefresh", st.session_state.graph_autorefresh, on_change=toggle_autorefresh):
    logger.info("Graph autorefresh ON")
  else:
    logger.info("Graph autorefresh OFF")
  st.session_state.show_source = st.toggle("Show response source", True)

  st.title("Useful links")
  st.markdown(f"""
  * [Spanner Graph database](https://console.cloud.google.com/spanner/instances/networktopology-instance/databases/networktopology-db/details/tables?invt=Abiyrw&project={project})
  * [Cluster Config status](https://console.cloud.google.com/kubernetes/config_management/packages?project={project})
  * [GitOps repository]({get_gitea_url()}/networkagent)
  * [Demo Scenario](https://docs.google.com/document/d/1gwCnLlgDaRWUv7I_hqd8aRv4B0ICsC7tj3pU7C8MRw0/edit?usp=sharing)"""
    )


# --------------------------------------------
# Main Panels
#---------------------------------------------
# Create columns with equal width
agentcolumn, graphcolumn, detailscolumn = st.columns([1,1,1]) 

# Agent Column
with agentcolumn:
  chatcontainer=st.container(height=745, border=True)
  # Display chat messages from history on app rerun
  for message in st.session_state.chat_history:
    if isinstance(message, AIMessage):
      with chatcontainer.chat_message("AI"):
        st.markdown(message.content)
    elif isinstance(message, HumanMessage):
      with chatcontainer.chat_message("Human"):
        st.markdown(message.content)

  # Accept user input
  if prompt := st.chat_input("Type your message here..."):
    st.session_state.chat_history.append(HumanMessage(content=prompt))

    with chatcontainer.chat_message("Human"):
        st.markdown(prompt)

    with chatcontainer.chat_message("AI"):
      response,source = st.session_state.agent.run(prompt)
      if st.session_state.show_source:
        st.markdown(f":blue[[{source}]] \n{response}")
      else:
        st.markdown(response)
      st.session_state.chat_history.append(AIMessage(content=response))

# Graph Column
with graphcolumn:
  graphcontainer = st.container(height=800, border=True)
  with graphcontainer:
    selected_elts = graphcontainer_fragment()

# Details Column
with detailscolumn:
  detailscontainer = st.container(height=500, border=True)
  logcontainer = st.container(height=300, border=True)
  with detailscontainer:
    graph_info = topology.display_selected_elements(st.session_state["selected_elts"])
    st.markdown(graph_info)
  with logcontainer:
    logcontainer_fragment()
