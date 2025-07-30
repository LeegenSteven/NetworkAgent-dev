# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.cloud import spanner
import logging
from utils.k8s import get_credentials
import json as json

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

#####################################################################################
# Graph stuff
#####################################################################################

# Connect to Spanner database
def spanner_connect():
  credentials = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

short_kinds = {
  'ComputeNetwork': 'net',
  'ComputeSubnetwork': 'subnet',
  'ComputeFirewall': 'FW',
  'ComputeInstance': 'VM',
  'ComputeRoute': 'route',
  'WireguardAppliance': 'VNF',
}

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

def update_node(id, kind, name, display_name, status, property):
  if node_uniq_ids[id]:
    node_uniq_ids[id]['status'] = status
    node_uniq_ids[id]['property'] = json.dumps(json.loads(property), indent=2)

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

def new_edge(edge_label, id, to_id, src_kind, tgt_kind):
  return {"group":"edges", "data": {"source": id, "target": to_id, "label": edge_label, "src_kind": src_kind, "tgt_kind": tgt_kind}, "selectable": True}

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
  added_node_ids = set()  # Track which nodes have already been added
  
  if success:
    for row in results:
      # Source node
      src_id = row[0]
      if src_id not in added_node_ids:
        elt = new_node(*row[0:6])
        if elt is not None: 
          elements.append(elt)
          added_node_ids.add(src_id)
      
      # Target node
      tgt_id = row[6]
      if tgt_id not in added_node_ids:
        elt = new_node(*row[6:12])
        if elt is not None: 
          elements.append(elt)
          added_node_ids.add(tgt_id)
      
      # Always add the edge
      label = row[12][0]
      src_kind = row[1]
      tgt_kind = row[7] 
      elements.append(new_edge(label, src_id, tgt_id, src_kind, tgt_kind))

  return elements, success

# Node info markdown template
NODE_INFO_TMPL="""
##### Node Id: {id} - {kind} 
* name : {name}
* status: {status}
* Property: 
```json
{property}
```
"""

# Works both for single and additivie node selection on the Graph
# Do not cache this function as the node in the graph database
# may have changed (e.g.status or property updated)
# @st.cache_data
def display_selected_elements(selected):
  logger.debug(f"Selected elements: {selected}")
  sn = selected['nodes']
  text = ""
  if not sn:
    return "Select a network node to display its properties"
  for id in sn:
    #print(f">>> ID: {id}")
    #print(f">>> {node_uniq_ids[id]['property']}")
    db_node = fetch_db_node(id)
    update_node(*db_node)
    text += NODE_INFO_TMPL.format(**node_uniq_ids[id])
  return text
