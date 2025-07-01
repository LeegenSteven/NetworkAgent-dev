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
def getNodePath(
   start_node_name: Annotated[str,"the name of the starting node in the path"], 
   end_node_name: Annotated[str,"the name of the ending node in the path"]
   ) -> List[Dict]:
    """
    Useful to find the list of network service or locations that connect a start and end pair of network services. 

    Returns:
        - a list of network services and locations representing the path elements between the start and end network services
    """

    logger.info("getNodePath with %s %s", start_node_name, end_node_name)

    gql_query = f"""
                GRAPH {GRAPH_NAME}
                MATCH p = ACYCLIC (start_ci_node:NetworkNode {{name: \'{start_node_name}\'}})
                    ( -[:IsConnectedTo]-> (sn_node {{kind:\'ComputeSubnetwork\'}}) <-[:IsConnectedTo]- (ci_node {{kind:\'ComputeInstance\'}}) ){{1,5}} (end_ci_node {{name: \'{end_node_name}\'}})
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
