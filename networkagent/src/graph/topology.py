from st_cytoscape import cytoscape
import numpy as np
import pandas as pd
from google.cloud import spanner
import logging
import google.auth
import os
import streamlit as st
import json as json


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

# FIXME : see how to set different colors
# for each kind of node
node_palette = [
  '#cc7722', '#ffe5b4', '#dda0dd', '#fffacd', '#e6e6fa',
  '#d2b48c', '#6a5acd', '#ffe4e1', '#6495ed', '#4b4b4b',
  '#ace1af', '#808000', '#e6e6e6', '#9671e8', '#6b8e23',
  '#654321', '#b0e0e6', '#1e1e1e', '#c8c8c8', '#cd853f']

kind_colors = {
  'ComputeNetwork': node_palette[0],
  'ComputeSubnetwork': node_palette[1],
  'ComputeFirewall': node_palette[2],
  'ComputeInstance': node_palette[3],
  'ComputeRoute': node_palette[4],
}

short_kinds = {
  'ComputeNetwork': 'net',
  'ComputeSubnetwork': 'subnet',
  'ComputeFirewall': 'FW',
  'ComputeInstance': 'VM',
  'ComputeRoute': 'route',
}


database = spanner_connect()
elements = []

node_uniq_ids = {}
dataplane_uids = []

def add_node(id, kind, name, display_name, status, property):
  if not (id in node_uniq_ids):
    node_uniq_ids[id] = {"id": id, "kind": kind, "name": name, "status": status, "property": json.dumps(json.loads(property), indent =2)}
    if kind in short_kinds:
      disp_name = f"{short_kinds[kind]} ({name})"
    else:
      disp_name = display_name
    elements.append({"data": {"id": id, "label": disp_name, "kind": kind, "name": name, "status": status}, "selectable": True})
    if 'dataplane' in display_name:
      dataplane_uids.append(id)

def add_edge(id, to_id):
  elements.append({"data": {"source": id, "target": to_id, "label": "isConnectedTo", "selectable": True}})

def build_network_graph(database):
  with database.snapshot() as snapshot:
    success = True
    try:
      results = snapshot.execute_sql(
        """GRAPH networkGraph
        MATCH (a)-[e:isConnectedTo]->(b)
        RETURN a.id AS a_id, a.kind AS a_kind, a.name AS a_name, a.display_name AS a_display_name, a.status AS a_status, TO_JSON_STRING(a.node_property) AS a_property,
        b.id AS b_id, b.kind AS b_kind, b.name AS b_name, b.display_name AS b_display_name, b.status AS b_status, TO_JSON_STRING(b.node_property) AS b_property""")
    except Exception as e:
      logger.error("SQL error: {}".format(e))
      success = False

    if success:
      for row in results:
        add_node(*row[0:6])
        add_node(*row[6:12])
        add_edge(row[0], row[6])
  return success

# Node info markdown template
NODE_INFO_TMPL="""
##### Node Id: {id} - {kind} 
* name : {name}
* status: {status}
* Property: 
```{property}```
"""

# Works both for single and additivie node selection on the Graph
@st.cache_data
def display_selected_elements(selected):
  sn = selected['nodes']
  text = ""
  if not sn:
    return "Select a network node to display its properties"
  for id in sn:
    #print(f">>> ID: {id}")
    #print(f">>> {node_uniq_ids[id]['property']}")
    text += NODE_INFO_TMPL.format(**node_uniq_ids[id])
  return text

build_network_graph(database)

stylesheet = [
    { "selector": "node", 
      "style": {
        "label": "data(label)",
        "width": 20,
        "height": 20
        }
    },
    { "selector": "edge",
      "style": {
        "width": 2,
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        },
    },
]

dataplane_uids 
layout = {"name": "fcose", "animationDuration": 3}
layout["alignmentConstraint"] = {"vertical": [dataplane_uids]}
"""layout["alignmentConstraint"] = {"horizontal": [["Z", "X", "M", "Y"]]}
layout["relativePlacementConstraint"] = [{"left": "X", "right": "Y"}]
layout["relativePlacementConstraint"].append({"top": "C1", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "C21", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C4"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C31"})"""
layout["nodeRepulsion"] = 50000


def create_cytoscape():
  return cytoscape(
      elements,
      stylesheet,
      height="450px",
      layout=layout,
      selection_type="single",
      user_panning_enabled=True,
      user_zooming_enabled=True,
      key="graph",
  )
