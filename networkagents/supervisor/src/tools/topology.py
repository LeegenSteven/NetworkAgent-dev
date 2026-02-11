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
from agent_library import get_credentials
import json as json

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'
GRAPH_NAME = 'networkGraph'

logger = logging.getLogger(__name__)

#####################################################################################
# Graph stuff
#####################################################################################

# Connect to Spanner database
def spanner_connect():
  credentials, _ = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

#####################################################################################
# Physical Topology
#####################################################################################

def fetch_physical_topology():
    """
    Fetch the physical network topology including routers, their interfaces, 
    links, and connectivity.
    
    Returns:
        dict: Physical topology with nodes (routers) and connections (links)
    """
    logger.info("Fetching physical network topology")
    
    topology = {
        'nodes': [],
        'connections': []
    }
    
    try:
        database = spanner_connect()
        
        # GQL query to get all routers with their interfaces and links
        gql_query = f"""
            GRAPH {GRAPH_NAME}
            MATCH (router:PhysicalRouter)
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            OPTIONAL MATCH (interface) -[:ConnectsTo]-> (link:PhysicalLink)
            OPTIONAL MATCH (link) -[:LinkedTo]-> (remote_interface:PhysicalInterface)
            OPTIONAL MATCH (remote_interface) <-[:HasInterface]- (remote_router:PhysicalRouter)
            RETURN 
                router.id AS router_id,
                router.name AS router_name,
                router.role AS router_role,
                router.status AS router_status,
                router.location_city AS router_city,
                router.location_lat AS router_lat,
                router.location_lon AS router_lon,
                interface.id AS interface_id,
                interface.name AS interface_name,
                link.id AS link_id,
                link.name AS link_name,
                remote_router.id AS remote_router_id,
                remote_router.name AS remote_router_name
        """
        
        logger.info("Executing GQL query for physical topology")
        
        routers = {}
        connections_set = set()
        
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            
            for row in results:
                router_id = row[0]
                router_name = row[1]
                router_role = row[2]
                router_status = row[3]
                router_city = row[4]
                router_lat = row[5]
                router_lon = row[6]
                interface_id = row[7]
                interface_name = row[8]
                link_id = row[9]
                link_name = row[10]
                remote_router_id = row[11]
                remote_router_name = row[12]
                
                # Add router to nodes if not already present
                if router_id not in routers:
                    router_location = {}
                    if router_city:
                        router_location['city'] = router_city
                    if router_lat is not None:
                        router_location['latitude'] = router_lat
                    if router_lon is not None:
                        router_location['longitude'] = router_lon
                    
                    routers[router_id] = {
                        'id': router_id,
                        'name': router_name,
                        'role': router_role if router_role else 'unknown',
                        'status': router_status if router_status else 'unknown',
                        'location': router_location if router_location else None,
                        'interfaces': []
                    }
                
                # Add interface info to router
                if interface_id and interface_id not in [iface['id'] for iface in routers[router_id]['interfaces']]:
                    routers[router_id]['interfaces'].append({
                        'id': interface_id,
                        'name': interface_name
                    })
                
                # Add connection if we have a link to another router
                if link_id and remote_router_id and router_id != remote_router_id:
                    # Create a sorted tuple to avoid duplicate connections
                    connection_key = tuple(sorted([router_id, remote_router_id]))
                    if connection_key not in connections_set:
                        connections_set.add(connection_key)
                        topology['connections'].append({
                            'id': link_id,
                            'name': link_name if link_name else f"link-{link_id[:8]}",
                            'source_router_id': router_id,
                            'source_router_name': router_name,
                            'target_router_id': remote_router_id,
                            'target_router_name': remote_router_name
                        })
        
        # Convert routers dict to list
        topology['nodes'] = list(routers.values())
        
        logger.info(f"Retrieved {len(topology['nodes'])} routers and {len(topology['connections'])} connections")
        return topology
        
    except Exception as e:
        logger.error(f"Error fetching physical topology: {e}", exc_info=True)
        return {'nodes': [], 'connections': [], 'error': str(e)}


def fetch_router_details(router_id):
    """
    Fetch detailed information for a specific router by ID.
    
    Args:
        router_id: The ID of the router to fetch
        
    Returns:
        dict: Router details including interfaces, config, and location
    """
    logger.info(f"Fetching router details for router_id: {router_id}")
    
    try:
        database = spanner_connect()
        
        # GQL query to get router details with all its interfaces
        gql_query = f"""
            GRAPH {GRAPH_NAME}
            MATCH (router:PhysicalRouter {{id: '{router_id}'}})
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            RETURN 
                router.id AS router_id,
                router.name AS router_name,
                router.vendor AS router_vendor,
                router.model AS router_model,
                router.role AS router_role,
                router.status AS router_status,
                router.location_city AS router_city,
                router.location_lat AS router_lat,
                router.location_lon AS router_lon,
                TO_JSON_STRING(router.config) AS router_config,
                interface.id AS interface_id,
                interface.name AS interface_name,
                interface.speed AS interface_speed,
                interface.media_type AS interface_media_type,
                interface.ip_address AS interface_ip,
                interface.mac_address AS interface_mac,
                interface.status AS interface_status
        """
        
        logger.info("Executing GQL query for router details")
        
        router_detail = None
        interfaces = []
        
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(gql_query)
            
            for row in results:
                # Build router details from first row
                if router_detail is None:
                    router_location = {}
                    if row[6]:  # router_city
                        router_location['city'] = row[6]
                    if row[7] is not None:  # router_lat
                        router_location['latitude'] = row[7]
                    if row[8] is not None:  # router_lon
                        router_location['longitude'] = row[8]
                    
                    router_config = {}
                    if row[9]:  # router_config
                        try:
                            router_config = json.loads(row[9])
                        except (json.JSONDecodeError, TypeError):
                            router_config = {}
                    
                    router_detail = {
                        'id': row[0],
                        'name': row[1],
                        'vendor': row[2] if row[2] else 'unknown',
                        'model': row[3] if row[3] else 'unknown',
                        'role': row[4] if row[4] else 'unknown',
                        'status': row[5] if row[5] else 'unknown',
                        'location': router_location if router_location else None,
                        'config': router_config,
                        'interfaces': []
                    }
                
                # Add interface if present
                if row[10]:  # interface_id
                    interfaces.append({
                        'id': row[10],
                        'name': row[11],
                        'speed': row[12],
                        'media_type': row[13],
                        'ip_address': row[14],
                        'mac_address': row[15],
                        'status': row[16] if row[16] else 'unknown'
                    })
        
        if router_detail is None:
            logger.warning(f"Router with ID {router_id} not found")
            return {'error': f'Router with ID {router_id} not found'}
        
        # Add unique interfaces to router details
        router_detail['interfaces'] = interfaces
        
        logger.info(f"Retrieved details for router {router_id} with {len(interfaces)} interfaces")
        return router_detail
        
        return router_detail
        
    except Exception as e:
        logger.error(f"Error fetching router details: {e}", exc_info=True)
        return {'error': str(e)}


def build_graph(database, edge_label=None):
    """
    Build the graph elements for the frontend.
    
    Args:
        database: Spanner database object (unused but kept for signature compatibility)
        edge_label: Optional label to filter edges (unused for now)
        
    Returns:
        tuple: (elements list, success boolean)
    """
    try:
        # For now, we only support physical topology which corresponds to 'network' view
        # TODO: Support 'resources' view if needed
        topology = fetch_physical_topology()
        elements = []
        
        if 'error' in topology:
             logger.error(f"Error in fetch_physical_topology: {topology['error']}")
             return [], False
        
        for node in topology.get('nodes', []):
            elements.append({
                'data': {
                    'id': node['id'],
                    'label': node['name'],
                    'type': 'router', 
                    'status': node.get('status', 'unknown'),
                    'role': node.get('role', 'unknown'),
                    'location': node.get('location')
                }
            })
            
        for conn in topology.get('connections', []):
            elements.append({
                'data': {
                    'id': conn['id'],
                    'source': conn['source_router_id'],
                    'target': conn['target_router_id'],
                    'label': conn.get('name', 'link')
                }
            })
            
        return elements, True
    except Exception as e:
        logger.error(f"Error building graph: {e}", exc_info=True)
        return [], False
