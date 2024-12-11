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
  credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/agent/networkagent.json"))[0]
  logger.info(credentials)
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
  'WireguardAppliance': 'VNF',
}

google_blue = "#4285F4"
google_red  = "#DB4437"
google_yellow = "#F4B400"
google_green  = "#0F9D58"

database = spanner_connect()

node_uniq_ids = {}
dataplane_uids = set()
service_uids = set()
appliance_uids = set()

def fetch_db_node(id):
  results = None
  with database.snapshot() as snapshot:
    try:
      sql = f"""GRAPH networkGraph
        MATCH (a:NetworkNode {{id: '{id}' }})
        RETURN a.id AS a_id, a.kind AS a_kind, a.name AS a_name, a.display_name AS a_display_name, 
        a.status AS a_status, TO_JSON_STRING(a.node_property) AS a_property"""
      results = snapshot.execute_sql(sql)
    except Exception as e:
      logger.error("SQL error: {}".format(e))

  # There should be only one row
  return results.one_or_none()

def update_graph_node(id, kind, name, display_name, status, property):
  if node_uniq_ids[id]:
    node_uniq_ids[id]['status'] = status
    node_uniq_ids[id]['property'] = json.dumps(json.loads(property), indent =2)

def new_node(id, kind, name, display_name, status, property):
  if not (id in node_uniq_ids):
    node_uniq_ids[id] = {"id": id, "kind": kind, "name": name, "status": status, "property": json.dumps(json.loads(property), indent =2)}
 
  if kind in short_kinds:
    disp_name = f"{short_kinds[kind]} ({name})"
  else:
    disp_name = display_name
  if 'dataplane' in display_name: dataplane_uids.add(id)
  if 'Service' in kind: service_uids.add(id)
  if 'Appliance' in kind: appliance_uids.add(id)

  return {"group": "nodes", "data": {"id": id, "label": disp_name, "kind": kind, "name": name, "status": status}, "selectable": True}

def new_edge(edge_label, id, to_id):
  return {"group":"edges", "data": {"source": id, "target": to_id, "label": edge_label}, "selectable": True}

def build_graph(database, edge_label):
  results = []
  success = True
  if edge_label is None:
    edge_pattern = 'e'
  else:
    edge_pattern = f"e:{edge_label}"

  with database.snapshot() as snapshot:
    try:
      results = snapshot.execute_sql(
        f"""GRAPH networkGraph
        MATCH (a)-[{edge_pattern}]->(b)
        RETURN a.id AS a_id, a.kind AS a_kind, a.name AS a_name, a.display_name AS a_display_name, a.status AS a_status, TO_JSON_STRING(a.node_property) AS a_property,
        b.id AS b_id, b.kind AS b_kind, b.name AS b_name, b.display_name AS b_display_name, b.status AS b_status, TO_JSON_STRING(b.node_property) AS b_property,
        LABELS(e) AS edge_label""")
    except Exception as e:
      logger.error("SQL error: {}".format(e))
      success = False

  elements = []
  if success:
    for row in results:
      elt = new_node(*row[0:6])
      if elt is not None: elements.append(elt)
      elt = new_node(*row[6:12])
      if elt is not None: elements.append(elt)
      elements.append(new_edge(row[12][0], row[0], row[6]))

  return elements, success

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
  logger.info(f"Selected elements: {selected}")
  sn = selected['nodes']
  text = ""
  if not sn:
    return "Select a network node to display its properties"
  for id in sn:
    #print(f">>> ID: {id}")
    #print(f">>> {node_uniq_ids[id]['property']}")
    db_node = fetch_db_node(id)
    print(db_node)
    update_graph_node(*db_node)
    text += NODE_INFO_TMPL.format(**node_uniq_ids[id])
  return text

stylesheet = [
    { "selector": "node", 
      "style": {
        "label": "data(label)",
        "width": 20,
        "height": 20,
        }
    },
    { "selector": "edge",
      "style": {
        "width": 3,
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        },
    },
]

"""layout["alignmentConstraint"] = {"horizontal": [["Z", "X", "M", "Y"]]}
layout["relativePlacementConstraint"] = [{"left": "X", "right": "Y"}]
layout["relativePlacementConstraint"].append({"top": "C1", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "C21", "bottom": "X"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C4"})
layout["relativePlacementConstraint"].append({"top": "X", "bottom": "C31"})"""

def hierarchical_layout(layout, elements):
  # Hierarchical view
  layout["relativePlacementConstraint"] = []
  for elt in elements:
    if elt['group'] == 'edges':
      layout["relativePlacementConstraint"].append({"top": elt['data']['source'], "bottom": elt['data']['target']})

def edge_color(elements, edge_label, color):
  for e in elements:
    if e['group'] == 'edges' and e['data']['label'] == edge_label:
      e['style'] = { 'line-color': color, 'target-arrow-color': color,'source-arrow-color': color  }

def create_network_cytoscape():
  elements, success = build_graph(database, 'isConnectedTo')
  layout = {"name": "fcose", "animationDuration": 3}
  layout["nodeRepulsion"] = 50000

  #hierarchical_layout(layout,elements)
  edge_color(elements, 'isConnectedTo', google_green)

  return cytoscape(
      elements,
      stylesheet,
      height="600px",
      layout=layout,
      selection_type="single",
      user_panning_enabled=True,
      user_zooming_enabled=True,
      key="network_graph",
  )

def create_resource_cytoscape():
  elements, success = build_graph(database, 'Manages')
  layout = {"name": "fcose", "animationDuration": 3}
  layout["nodeRepulsion"] = 50000

  hierarchical_layout(layout,elements)
  edge_color(elements, 'Manages', google_blue)

  return cytoscape(
      elements,
      stylesheet,
      height="600px",
      layout=layout,
      selection_type="single",
      user_panning_enabled=True,
      user_zooming_enabled=True,
      key="resource_graph",
  )

def create_combined_cytoscape():
  elements, success = build_graph(database, None)
  layout = {"name": "fcose", "animationDuration": 3}
  layout["nodeRepulsion"] = 50000

  hierarchical_layout(layout,elements)
  edge_color(elements, 'Manages', google_blue)
  edge_color(elements, 'isConnectedTo', google_green)

  return cytoscape(
      elements,
      stylesheet,
      height="600px",
      layout=layout,
      selection_type="single",
      user_panning_enabled=True,
      user_zooming_enabled=True,
      key="combined_graph",
  )