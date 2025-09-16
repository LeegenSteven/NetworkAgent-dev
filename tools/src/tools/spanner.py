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

from typing import Annotated, Dict, List
from google.cloud import spanner
import utils.globals as globals
from mcp.types import ToolAnnotations
from utils.k8s import get_credentials
import collections
import logging
import json

GRAPH_NAME = 'networkGraph'
SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
def spanner_connect():
  credentials = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

############################################################
# Topology tools
############################################################

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_parent_network_service(name: Annotated[str,"the name of the ComputeInstance"]) -> Dict:
    """
    Find the network service kind for the given ComputeInstance. 
    Args:
        name: the name of the ComputeInstance

    Returns:
        Dict: Network Service description with kind, configuration and status
    """
    logger.info(f"find parent kind for {name}")

    # Build GQL query to get detailed information about the node and related resources
    gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH (node:NetworkNode {{name: \'{name}\', kind: \'ComputeInstance\'}})
                OPTIONAL MATCH (node) <-[:Manages]- (parent_node:NetworkNode)
                RETURN 
                    parent_node.name AS node_name, 
                    parent_node.kind AS node_kind
            """
    logger.info("Executing GQL query: %s", gql_query)

    parent_node = {}
    results_list = []
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            logger.info("Query executed successfully")
            
            for row in results:
                logger.info("Found node details: %s", row)
                # Check if parent_node data exists (not NULL)
                if row[0] is not None and row[1] is not None:
                    results_list.append({
                        'name': row[0],  # Fixed: row[0] is parent_node.name
                        'kind': row[1],  # Fixed: row[1] is parent_node.kind
                    })
            
            # Validate that we have exactly one result as expected
            if len(results_list) == 0:
                logger.info("No parent found for node %s", name)
                return {}
            elif len(results_list) == 1:
                parent_node = results_list[0]
                logger.info("Retrieved parent for node %s: %s", name, parent_node)
                return parent_node
            else:
                logger.warning("Multiple parents found for node %s (expected exactly one): %d results", name, len(results_list))
                # Return the first parent but log the issue
                parent_node = results_list[0]
                logger.warning("Returning first parent: %s", parent_node)
                return parent_node
                
    except Exception as e:
        logger.error("SQL error in get_parent_network_service: {}".format(e))
        return {}

    logger.info("No parent found for node %s", name)
    return {}

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_node_details(
        name: Annotated[str,"the name of the ComputeInstance to get details for"]
    ) -> List[Dict]:
    """
    Get ComputeInstance Details including configuration and status. 

    Returns:
        - a list of network service and children resources and their status
    """
    logger.info("get_node_details for %s", name)
    
    # Build GQL query to get detailed information about the node and related resources
    gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH (node:NetworkNode {{name: \'{name}\', kind: \'ComputeInstance\'}})
                OPTIONAL MATCH (node) -[:isConnectedTo]-> (connected_resource:NetworkNode)
                OPTIONAL MATCH (node) <-[:isConnectedTo]- (parent_resource:NetworkNode)
                RETURN 
                    node.id AS node_id,
                    node.name AS node_name, 
                    node.kind AS node_kind,
                    node.status AS node_status,
                    node.display_name AS node_display_name,
                    node.self_link AS node_self_link,
                    TO_JSON_STRING(node.node_property) AS node_property,
                    ARRAY_AGG(DISTINCT connected_resource.name) AS connected_resources,
                    ARRAY_AGG(DISTINCT parent_resource.name) AS parent_resources
            """
    logger.info("Executing GQL query: %s", gql_query)
    
    node_details = []
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            logger.info("Query executed successfully")
            
            for row in results:
                logger.info("Found node details: %s", row)
                
                # Parse the JSON property to extract specific fields
                node_property = {}
                if row[6] is not None:
                    try:
                        node_property = json.loads(row[6])
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Failed to parse node_property JSON: %s", e)
                        node_property = {}
                
                # Extract node details
                node_detail = {
                    'id': row[0],
                    'name': row[1],
                    'kind': row[2],
                    'status': row[3] if row[3] is not None else 'Unknown',
                    'display_name': row[4] if row[4] is not None else 'Unknown',
                    'self_link': row[5] if row[5] is not None else 'Unknown',
                    'kubernetes_instance': node_property,
                }
                
                node_details.append(node_detail)
                
    except Exception as e:
        logger.error("SQL error in get_node_details: {}".format(e))
        return []

    logger.info("Retrieved details for node %s: %d records", name, len(node_details))
    return node_details


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_connected_nodes(name: Annotated[str,"the name of the ComputeInstance"], 
                        hops: Annotated[int,"the number of hops away from the starting node, between 1-3"]):
    """
    Find the ComputeInstance nodes connected to the given ComputeInstance a number of 'hops' away

    Returns:
        - a list of ComputeInstance names only
    """
    logger.info("get_connected_nodes for %s and %s hops away", name, hops)
    
    # Validate input parameters
    if hops < 1:
        logger.warning("Invalid hops value: %s. Must be >= 1", hops)
        return []
    
    # Build GQL query to find connected nodes within specified hops
    # Limit hops to maximum of 3 for performance reasons
    if hops > 3:
        logger.warning("Limiting hops to 3 for performance. Requested: %s", hops)
        hops = 3
    
    # Build the dynamic MATCH pattern based on number of hops
    match_pattern = f"(start_node:NetworkNode {{name: '{name}', kind: 'ComputeInstance'}})"
    where_conditions = [f"connected_node.name != '{name}'"]
    
    for i in range(hops):
        # Add subnet connection
        subnet_var = f"sn{i+1}" if hops > 1 else "sn_node"
        match_pattern += f"\n                          -[:isConnectedTo]-> ({subnet_var} {{kind:'ComputeSubnetwork'}})"
        
        # Add compute instance connection (except for the last hop)
        if i < hops - 1:
            hop_var = f"hop{i+1}"
            match_pattern += f"\n                          <-[:isConnectedTo]- ({hop_var} {{kind:'ComputeInstance'}})"
            where_conditions.append(f"{hop_var}.name != '{name}'")
        else:
            # Final connection to the target node
            match_pattern += f"\n                          <-[:isConnectedTo]- (connected_node:NetworkNode {{kind:'ComputeInstance'}})"
    
    # Combine WHERE conditions
    where_clause = " AND ".join(where_conditions)
    
    # Build the complete GQL query
    gql_query = f"""
                    GRAPH {GRAPH_NAME}
                    MATCH {match_pattern}
                    WHERE {where_clause}
                    RETURN DISTINCT connected_node.id AS id, connected_node.name AS name, connected_node.kind AS kind
                """
    logger.info("Executing GQL query: %s", gql_query)
    
    connected_nodes = []
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            logger.info("Query executed successfully")
            
            for row in results:
                logger.info("Found connected node: %s", row)
                connected_nodes.append({
                    'id': row[0],
                    'name': row[1], 
                    'kind': row[2]
                })
                        
    except Exception as e:
        logger.error("SQL error in get_connected_nodes: {}".format(e))
        return []

    logger.info("Found %d connected nodes within %d hops of %s", len(connected_nodes), hops, name)
    return connected_nodes


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_node_path(
   start_node_name: Annotated[str,"the name of the starting network service in the path"], 
   end_node_name: Annotated[str,"the name of the ending networkservice in the path"]
   ) -> List[Dict]:
    """
    Useful to find the list of network service or locations that connect a start and end pair of network services (ComputeInstances). 

    Returns:
        - a list of network services (ComputeInstances) and locations (ComputeSubnetworks) names
    """

    logger.info("get_node_path with %s %s", start_node_name, end_node_name)

    gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH p = ACYCLIC (start_ci_node:NetworkNode {{name: \'{start_node_name}\'}})
                    ( -[:isConnectedTo]-> (sn_node {{kind:\'ComputeSubnetwork\'}}) <-[:isConnectedTo]- (ci_node {{kind:\'ComputeInstance\'}}) ){{1,5}} (end_ci_node {{name: \'{end_node_name}\'}})
                WHERE
                start_ci_node.kind = \'ComputeInstance\' AND
                end_ci_node.kind = \'ComputeInstance\' 
                RETURN SAFE_TO_JSON(p) AS result_paths
            """
    logger.info(gql_query)
    
    path_elements = []
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            logger.info(results)
            
            for row in results:
                logger.info("adding node details")
                logger.info("Row[0] type: %s", type(row[0]))
                logger.info("Row[0] content: %s", row[0])

                json_object = row[0]._array_value  # This is google.cloud.spanner_v1.data_types.JsonObject
                
                logger.info("JsonObject type: %s", type(json_object))

                for n in json_object:
                    logger.info(n)
                    if n.get('element_definition_name') == "NetworkNode":
                        path_elements.append({
                            'id': n.get('properties').get('id'),
                            'name': n.get('properties').get('name'),
                            'kind': n.get('properties').get('kind'),
                        })
                        
    except Exception as e:
        logger.error("SQL error: {}".format(e))
        # Return empty list on error instead of tuple with success flag
        return []

    return path_elements


@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_nodes_networking_by_kind(
   kind: Annotated[str,"the kind of the network service"]
   ) -> List[Dict]:
    """
    Useful to find the network configuration details for a kind of network service. This can be useful to find network service instances with a given ip scheme. 

    Returns:
        - a list of network service instances of the kind requested with a summary of its network configuration, e.g. ip addresses
    """

    logger.info("get_nodes_networking_by_kind for kind=%s", kind)

    # Build GQL query to find nodes of specified kind and their managed ComputeAddress resources
    gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH (parent:NetworkNode {{kind: \'{kind}\'}})
                OPTIONAL MATCH (parent) -[:Manages]-> (address:NetworkNode {{kind: \'ComputeAddress\'}})
                RETURN 
                    parent.id AS parent_id,
                    parent.name AS parent_name,
                    parent.kind AS parent_kind,
                    address.id AS address_id,
                    address.name AS address_name,
                    TO_JSON_STRING(address.node_property) AS address_property
            """
    logger.info("Executing GQL query: %s", gql_query)

    networking_results = []
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            logger.info("Query executed successfully")
            
            for row in results:
                logger.info("Processing row: %s", row)
                
                result_entry = {
                    'parent': {
                        'id': row[0],
                        'name': row[1],
                        'kind': row[2]
                    }
                }
                
                # Add address information if present
                if row[3] is not None:  # address_id
                    address_property = {}
                    if row[5] is not None:  # address_property
                        try:
                            address_property = json.loads(row[5])
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.warning("Failed to parse address property JSON: %s", e)
                            address_property = {}
                    
                    result_entry['address'] = {
                        'id': row[3],
                        'name': row[4],
                        'kind': 'ComputeAddress',
                        'node_property': address_property
                    }
                
                networking_results.append(result_entry)
                
    except Exception as e:
        logger.error("SQL error in get_nodes_networking_by_kind: {}".format(e))
        return []

    logger.info("Retrieved networking data for %d entries of kind %s", len(networking_results), kind)
    return networking_results
