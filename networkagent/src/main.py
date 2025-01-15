import hmac
import logging
import os
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.DEBUG, format=log_format)
logger = logging.getLogger(__name__)

import streamlit as st
# set_page_config must be executed before any other streamlit code.
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

import utils.st_extension as st_ext
from streamlit.components.v1 import html
from streamlit_autorefresh import st_autorefresh


from langchain_core.messages import AIMessage, HumanMessage
from agent.networkagent import NetworkAgent
import graph.topology as topology

# From https://ploomber.io/blog/streamlit-password/
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        # If you use .streamlit/secrets.toml, replace os.environ.get with st.secrets["STREAMLIT_PASSWORD"]
        if hmac.compare_digest(st.session_state["password"], os.environ.get("WEBAPPS_PWD", "")):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect")
    return False


def reset_chat_history():
  """
  Resets the chat history in the Streamlit session state.
  """
  st.session_state.chat_history = [
    AIMessage(content="Hello, I am your network assistant. How can I help you ?"),
  ]
  st.session_state.agent = NetworkAgent()

# Setup Agent and chat history
if "agent" not in st.session_state:
  st.session_state.agent = NetworkAgent()
if "chat_history" not in st.session_state:
  reset_chat_history()

project = os.environ.get("GOOGLE_PROJECT")

st.title("Autonomous Network Agent")

if not check_password():
  st.stop()

# --------------------------------------------
# Side Bar (Settings and links)
#---------------------------------------------

with st.sidebar:
  st.title("Settings")
  if st.button("Reset chat"):
    reset_chat_history()
  if st.toggle("Graph autorefresh"):
    count = st_autorefresh(interval=3000, key="counter")
  st.title("Useful links")
  st.markdown(f"""
  * [Spanner Graph database](https://console.cloud.google.com/spanner/instances/networktopology-instance/databases/networktopology-db/details/tables?invt=Abiyrw&project={project})
  * [Cluster Config status](https://console.cloud.google.com/kubernetes/config_management/packages?project={project})
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
  graphcontainer = st.container(height=800, border=True)
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
  detailscontainer = st.container(height=800, border=True)
  with detailscontainer:
    graph_info = topology.display_selected_elements(selected_elts)
    st.markdown(graph_info)
