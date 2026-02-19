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
import datetime

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

def fetch_physical_topology(timestamp_str: str = None):
    """
    Fetch the physical network topology including routers, their interfaces, 
    links, connectivity, and embeddings data.
    
    Args:
        timestamp_str: Optional ISO-8601 timestamp string. If provided, fetches historical
                      snapshot at that point in time. If None, fetches latest.
    
    Returns:
        dict: Physical topology with nodes (routers) and connections (links),
              including embeddings data (MSE and RCA) for each router and interface
    """
    logger.info(f"Fetching physical network topology (timestamp={timestamp_str})")
    
    topology = {
        'nodes': [],
        'connections': []
    }
    
    try:
        database = spanner_connect()
        
        # Determine the timestamp to use for queries
        target_timestamp = None
        if timestamp_str:
            try:
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1] + '+00:00'
                target_timestamp = datetime.datetime.fromisoformat(timestamp_str)
                logger.info(f"Using historical timestamp: {target_timestamp}")
            except ValueError as e:
                logger.error(f"Invalid timestamp format: {e}")
                return {'nodes': [], 'connections': [], 'error': 'Invalid timestamp format'}
        
        # GQL query to get all routers with their interfaces and links
        # For now, fetch latest topology (temporal filtering would require more complex logic)
        gql_query = f"""
            GRAPH {GRAPH_NAME}
            MATCH (router:PhysicalRouter)
            WHERE router.valid_end_ts IS NULL
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            WHERE interface.valid_end_ts IS NULL AND interface.name != 'eth0'
            OPTIONAL MATCH (interface) -[:ConnectsTo]-> (link:PhysicalLink)
            WHERE link.valid_end_ts IS NULL
            OPTIONAL MATCH (link) -[:LinkedTo]-> (remote_interface:PhysicalInterface)
            WHERE remote_interface.valid_end_ts IS NULL AND remote_interface.name != 'eth0'
            OPTIONAL MATCH (remote_interface) <-[:HasInterface]- (remote_router:PhysicalRouter)
            WHERE remote_router.valid_end_ts IS NULL
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
        
        # Now fetch embeddings for all routers and their interfaces
        logger.info(f"Fetching embeddings for {len(routers)} routers")
        _add_embeddings_to_routers(database, routers, target_timestamp)
        
        # Convert routers dict to list
        topology['nodes'] = list(routers.values())
        
        logger.info(f"Retrieved {len(topology['nodes'])} routers and {len(topology['connections'])} connections with embeddings")
        return topology
        
    except Exception as e:
        logger.error(f"Error fetching physical topology: {e}", exc_info=True)
        return {'nodes': [], 'connections': [], 'error': str(e)}


def _add_embeddings_to_routers(database, routers, target_timestamp=None):
    """
    Add embeddings data to routers dict in-place.
    
    Args:
        database: Spanner database connection
        routers: Dict of routers keyed by router_id
        target_timestamp: Optional datetime for historical embeddings
    """
    try:
        # Build query to fetch router embeddings
        if target_timestamp:
            # Historical: find embeddings closest to target timestamp
            router_embedding_query = """
                SELECT 
                    e.node_id,
                    e.anomaly_score,
                    TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                WHERE e.node_id IN UNNEST(@router_ids)
                  AND e.node_type = 'PhysicalRouter'
                  AND e.timestamp <= @target_timestamp
                ORDER BY e.node_id, e.timestamp DESC
            """
            
            interface_embedding_query = """
                SELECT 
                    e.node_id,
                    e.anomaly_score,
                    TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE i.router_id IN UNNEST(@router_ids)
                  AND e.node_type = 'PhysicalInterface'
                  AND e.timestamp <= @target_timestamp
                ORDER BY i.router_id, e.node_id, e.timestamp DESC
            """
        else:
            # Latest embeddings
            router_embedding_query = """
                SELECT 
                    e.node_id,
                    e.anomaly_score,
                    TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                WHERE e.node_id IN UNNEST(@router_ids)
                  AND e.node_type = 'PhysicalRouter'
                  AND e.timestamp = (
                      SELECT MAX(timestamp) FROM NodeEmbedding 
                      WHERE node_id = e.node_id
                  )
            """
            
            interface_embedding_query = """
                SELECT 
                    i.router_id,
                    e.node_id AS interface_id,
                    i.name AS interface_name,
                    e.anomaly_score,
                    TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation,
                    e.timestamp
                FROM NodeEmbedding e
                JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE i.router_id IN UNNEST(@router_ids)
                  AND e.node_type = 'PhysicalInterface'
                  AND i.valid_end_ts IS NULL
                  AND e.timestamp = (
                      SELECT MAX(timestamp) FROM NodeEmbedding 
                      WHERE node_id = e.node_id
                  )
            """
        
        router_ids = list(routers.keys())
        params = {"router_ids": router_ids}
        param_types = {"router_ids": spanner.param_types.Array(spanner.param_types.STRING)}
        
        if target_timestamp:
            params["target_timestamp"] = target_timestamp
            param_types["target_timestamp"] = spanner.param_types.TIMESTAMP
        
        # Fetch router embeddings
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(router_embedding_query, params=params, param_types=param_types)
            seen_routers = set()
            
            for row in results:
                router_id = row[0]
                if target_timestamp and router_id in seen_routers:
                    continue  # Skip older entries, we want the latest before target
                seen_routers.add(router_id)
                
                if router_id in routers:
                    anomaly_explanation = None
                    if row[2]:
                        try:
                            anomaly_explanation = json.loads(row[2])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    routers[router_id]['router_mse'] = row[1]  # anomaly_score
                    routers[router_id]['router_rca'] = anomaly_explanation
                    routers[router_id]['embedding_timestamp'] = row[3].isoformat() if row[3] else None
        
        # Fetch interface embeddings
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(interface_embedding_query, params=params, param_types=param_types)
            
            for row in results:
                if target_timestamp:
                    router_id = row[0] if len(row) > 5 else None
                    interface_id = row[1] if len(row) > 5 else row[0]
                    interface_name = row[2] if len(row) > 5 else None
                    anomaly_score = row[3] if len(row) > 5 else row[1]
                    anomaly_explanation_str = row[4] if len(row) > 5 else row[2]
                else:
                    router_id = row[0]
                    interface_id = row[1]
                    interface_name = row[2]
                    anomaly_score = row[3]
                    anomaly_explanation_str = row[4]
                
                if router_id and router_id in routers:
                    if 'interface_mses' not in routers[router_id]:
                        routers[router_id]['interface_mses'] = {}
                    
                    anomaly_explanation = None
                    if anomaly_explanation_str:
                        try:
                            anomaly_explanation = json.loads(anomaly_explanation_str)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    
                    routers[router_id]['interface_mses'][interface_id] = {
                        'mse': anomaly_score,
                        'name': interface_name,
                        'rca': anomaly_explanation
                    }
        
        logger.info(f"Added embeddings to {len([r for r in routers.values() if 'router_mse' in r])} routers")
        
    except Exception as e:
        logger.error(f"Error fetching embeddings: {e}", exc_info=True)
        # Continue without embeddings rather than failing


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
            WHERE router.valid_end_ts IS NULL
            OPTIONAL MATCH (router) -[:HasInterface]-> (interface:PhysicalInterface)
            WHERE interface.valid_end_ts IS NULL AND interface.name != 'eth0'
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
        
    except Exception as e:
        logger.error(f"Error fetching router details: {e}", exc_info=True)
        return {'error': str(e)}


def fetch_node_embeddings(node_id):
    """
    Fetch the latest embeddings for a router and its interfaces.
    
    Args:
        node_id: The ID of the router to fetch embeddings for
        
    Returns:
        dict: Embeddings data including router embedding and interface embeddings with MSE
    """
    logger.info(f"Fetching embeddings for node_id: {node_id}")
    
    try:
        database = spanner_connect()
        
        # Query to get the latest embedding for the router
        router_embedding_query = """
            SELECT 
                e.node_id,
                e.node_type,
                e.anomaly_score,
                e.embedding,
                e.timestamp,
                TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation
            FROM NodeEmbedding e
            WHERE e.node_id = @node_id
            ORDER BY e.timestamp DESC
            LIMIT 1
        """
        
        # Query to get the latest embeddings for all interfaces of this router
        interface_embeddings_query = """
            SELECT 
                i.id AS interface_id,
                i.name AS interface_name,
                e.anomaly_score,
                e.embedding,
                e.timestamp,
                TO_JSON_STRING(e.anomaly_explanation) AS anomaly_explanation
            FROM PhysicalInterface i
            JOIN NodeEmbedding e ON i.id = e.node_id
            WHERE i.router_id = @node_id
              AND i.valid_end_ts IS NULL
              AND e.timestamp = (
                  SELECT MAX(timestamp) 
                  FROM NodeEmbedding 
                  WHERE node_id = i.id
              )
            ORDER BY i.name
        """
        
        result = {
            'node_id': node_id,
            'router_embedding': None,
            'interface_embeddings': []
        }
        
        params = {"node_id": node_id}
        param_types = {"node_id": spanner.param_types.STRING}
        
        # Fetch router embedding
        with database.snapshot() as snapshot:
            router_results = snapshot.execute_sql(
                router_embedding_query, 
                params=params, 
                param_types=param_types
            )
            
            for row in router_results:
                anomaly_explanation = None
                if row[5]:
                    try:
                        anomaly_explanation = json.loads(row[5])
                    except (json.JSONDecodeError, TypeError):
                        anomaly_explanation = None
                
                result['router_embedding'] = {
                    'node_id': row[0],
                    'node_type': row[1],
                    'mse': row[2],  # anomaly_score is the MSE
                    'embedding_vector': row[3],  # The actual embedding array
                    'timestamp': row[4].isoformat() if row[4] else None,
                    'anomaly_explanation': anomaly_explanation
                }
                break
        
        # Fetch interface embeddings
        with database.snapshot() as snapshot:
            interface_results = snapshot.execute_sql(
                interface_embeddings_query,
                params=params,
                param_types=param_types
            )
            
            for row in interface_results:
                anomaly_explanation = None
                if row[5]:
                    try:
                        anomaly_explanation = json.loads(row[5])
                    except (json.JSONDecodeError, TypeError):
                        anomaly_explanation = None
                
                result['interface_embeddings'].append({
                    'interface_id': row[0],
                    'interface_name': row[1],
                    'mse': row[2],  # anomaly_score is the MSE
                    'embedding_vector': row[3],  # The actual embedding array
                    'timestamp': row[4].isoformat() if row[4] else None,
                    'anomaly_explanation': anomaly_explanation
                })
        
        logger.info(f"Retrieved embeddings for node {node_id}: router={result['router_embedding'] is not None}, interfaces={len(result['interface_embeddings'])}")
        return result
        
    except Exception as e:
        logger.error(f"Error fetching node embeddings: {e}", exc_info=True)
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

#####################################################################################
# Anomalies & Snapshots
#####################################################################################

def fetch_snapshots():
    """
    Fetch available snapshots from the Spanner database.
    """
    logger.info("Fetching snapshots")
    try:
        database = spanner_connect()
        query = "SELECT DISTINCT timestamp FROM NodeEmbedding ORDER BY timestamp DESC LIMIT 100"
        
        snapshots = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query)
            for row in results:
                ts = row[0]
                if ts:
                    snapshots.append(ts.isoformat())
                    
        return {"snapshots": snapshots}
    except Exception as e:
        logger.error(f"Failed to fetch snapshots: {e}", exc_info=True)
        return {"error": str(e)}

def fetch_anomalies(limit: int = 50, timestamp_str: str = None):
    """
    Fetch top anomalies from NodeEmbedding.
    """
    logger.info(f"Fetching anomalies (limit={limit}, timestamp={timestamp_str})")
    try:
        database = spanner_connect()
        
        params = {"limit": limit}
        param_types = {"limit": spanner.param_types.INT64}
        
        if timestamp_str:
            try:
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1] + '+00:00'
                ts = datetime.datetime.fromisoformat(timestamp_str)
                params["timestamp"] = ts
                param_types["timestamp"] = spanner.param_types.TIMESTAMP
                
                query = """
                    SELECT e.node_id, e.node_type, e.anomaly_score, e.anomaly_explanation AS root_cause, 
                           COALESCE(r.name, i.name) as name, e.timestamp
                    FROM NodeEmbedding e
                    LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                    LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                    WHERE e.timestamp = @timestamp
                    ORDER BY e.anomaly_score DESC
                    LIMIT @limit
                """
            except ValueError:
                return {"error": "Invalid timestamp format"}
        else:
            query = """
                SELECT e.node_id, e.node_type, e.anomaly_score, e.anomaly_explanation AS root_cause, 
                       COALESCE(r.name, i.name) as name, e.timestamp
                FROM NodeEmbedding e
                LEFT JOIN PhysicalRouter r ON e.node_id = r.id
                LEFT JOIN PhysicalInterface i ON e.node_id = i.id
                WHERE e.timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
                ORDER BY e.anomaly_score DESC
                LIMIT @limit
            """
            
        anomalies = []
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=params, param_types=param_types)
            for row in results:
                anomalies.append({
                    "node_id": row[0],
                    "node_type": row[1],
                    "anomaly_score": row[2],
                    "root_cause": row[3],
                    "name": row[4] if row[4] else "Unknown",
                    "timestamp": row[5].isoformat() if row[5] else None
                })
                
        return {"anomalies": anomalies}
    except Exception as e:
        logger.error(f"Failed to fetch anomalies: {e}", exc_info=True)
        return {"error": str(e)}
