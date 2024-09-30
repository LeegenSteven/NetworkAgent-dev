from st_cytoscape import cytoscape
import numpy as np
import pandas as pd
from google.cloud import spanner
import logging
import google.auth
import os
import streamlit as st

logger = logging.getLogger(__name__)

#####################################################################################
# Graph stuff
#####################################################################################

# Connect to Spanner database
@st.cache_resource
def spanner_connect():
  credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/tools/networkagent.json"))[0]
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()

def get_nodes():
  def sql_get_network_nodes(transaction):
    sql = "SELECT * from NetworkNode"
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_get_network_nodes)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  return success

generating_model = """
Z <-- N
C1 <-- N
C21 <-- N
C31 <-- N
C33 <-- N
C22 <-- C21 + N
C32 <-- C31 + C33 + N
X <-- Z + C1 + C21 + C31 + N
M <-- X + N
Y <-- M + C1 + C22 + C33 + N
C4 <-- X + Y + N
"""

def generate_data():
    np.random.seed(seed=0)
    d = {}
    nodes = set()
    edges = set()
    for line in generating_model.split("\n"):
        if " <-- " in line:
            left, right = line.split(" <-- ")
            right_terms = right.split(" + ")
            nodes.add(left)
            for node in right_terms:
                if node != "N":
                    nodes.add(node)
                    edges.add((node, left))
    return nodes, edges

nodes, edges = generate_data()
elements = []
for node in nodes:
    elements.append(
        {
            "data": {"id": node},
            "selected": node == "X",
            "selectable": node not in ["X", "Y"],
        }
    )
for edge in edges:
    elements.append(
        {
            "data": {
                "source": edge[0],
                "target": edge[1],
                "id": f"{edge[0]}-{edge[1]}",
            },
            "selectable": True,
        }
    )
stylesheet = [
    {"selector": "node", "style": {"label": "data(id)", "width": 20, "height": 20}},
    {
        "selector": "edge",
        "style": {
            "width": 2,
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
        },
    },
]

layout = {"name": "fcose", "animationDuration": 0}
layout["alignmentConstraint"] = {"horizontal": [["Z", "X", "M", "Y"]]}
layout["relativePlacementConstraint"] = [{"left": "X", "right": "Y"}]
layout["relativePlacementConstraint"].append({"top": "C1", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "C21", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C4"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C31"})
layout["nodeRepulsion"] = 50000

def create_cytoscape():
    return cytoscape(
        elements,
        stylesheet,
        height="450px",
        layout=layout,
        selection_type="additive",
        user_panning_enabled=True,
        user_zooming_enabled=False,
        key="graph",
    )
