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

import logging
from utils.compute import *
# from utils.request_throttler import throttled, throttled_call
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner

SQL_TEMPLATES = {
  # --- Knowledge Graph tables ---
  'create_kg_res_node': "INSERT KgResourceDescriptionNode (id, content)"
                        " VALUES (@id, @content)",
  'update_kg_res_node': "UPDATE KgResourceDescriptionNode SET content = @content WHERE id = @id",
  'delete_kg_res_node': "DELETE FROM KgResourceDescriptionNode WHERE id = @id",
  'exist_kg_res_node' : "SELECT id FROM KgResourceDescriptionNode WHERE id = '{id}'",

  # --- Network topology tables ---
  'upsert_phy_router': "INSERT OR UPDATE PhysicalRouter (id, name, vendor, model, location_city, location_lat, location_lon, role, status, config, last_updated) VALUES (@id, @name, @vendor, @model, @location_city, @location_lat, @location_lon, @role, @status, @config, PENDING_COMMIT_TIMESTAMP())",
  'delete_phy_router': "DELETE FROM PhysicalRouter WHERE id = @id",
  'get_router_id_by_name': "SELECT id FROM PhysicalRouter WHERE name = @name",

  'upsert_phy_interface': "INSERT OR UPDATE PhysicalInterface (id, router_id, name, speed, media_type, ip_address, mac_address, status, config, last_updated) VALUES (@id, @router_id, @name, @speed, @media_type, @ip_address, @mac_address, @status, @config, PENDING_COMMIT_TIMESTAMP())",
  'delete_phy_interface_by_router': "DELETE FROM PhysicalInterface WHERE router_id = @router_id",

  'upsert_customer': "INSERT OR UPDATE Customer (id, name, type, properties, last_updated) VALUES (@id, @name, @type, @properties, PENDING_COMMIT_TIMESTAMP())",
  
  'upsert_l3vpn': "INSERT OR UPDATE L3VPNService (id, customer_id, name, service_type, topology, status, config, last_updated) VALUES (@id, @customer_id, @name, @service_type, @topology, @status, @config, PENDING_COMMIT_TIMESTAMP())",
  'delete_l3vpn': "DELETE FROM L3VPNService WHERE id = @id",

  'upsert_vrf': "INSERT OR UPDATE VRF (id, router_id, vpn_id, name, rd, status, config, last_updated) VALUES (@id, @router_id, @vpn_id, @name, @rd, @status, @config, PENDING_COMMIT_TIMESTAMP())",
  'delete_vrf_by_vpn': "DELETE FROM VRF WHERE vpn_id = @vpn_id",
  
  'upsert_bgp': "INSERT OR UPDATE BGPSession (id, vrf_id, local_as, remote_as, peer_ip, status, config, last_updated) VALUES (@id, @vrf_id, @local_as, @remote_as, @peer_ip, @status, @config, PENDING_COMMIT_TIMESTAMP())",
  'delete_bgp_by_vpn': "DELETE FROM BGPSession WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id = @vpn_id)",

  'upsert_subnet': "INSERT OR UPDATE LogicalSubnet (id, cidr, network_type, description, properties, last_updated) VALUES (@id, @cidr, @network_type, @description, @properties, PENDING_COMMIT_TIMESTAMP())",

  'upsert_phy_link': "INSERT OR UPDATE PhysicalLink (id, name, bandwidth, status, properties, last_updated) VALUES (@id, @name, @bandwidth, @status, @properties, PENDING_COMMIT_TIMESTAMP())",
  'delete_phy_link': "DELETE FROM PhysicalLink WHERE id = @id",

  'upsert_interface_link': "INSERT OR IGNORE Interface_Link (interface_id, link_id) VALUES (@interface_id, @link_id)",
  'delete_interface_link': "DELETE FROM Interface_Link WHERE interface_id = @interface_id OR link_id = @link_id",

  'upsert_subnet_assoc': "INSERT OR IGNORE Subnet_Association (entity_id, subnet_id, entity_type) VALUES (@entity_id, @subnet_id, @entity_type)",
  'delete_subnet_assoc': "DELETE FROM Subnet_Association WHERE entity_id = @entity_id OR subnet_id = @subnet_id",

  'upsert_service_perf': "INSERT OR UPDATE ServicePerformance (id, service_type, response_time_ms, timestamp, userid, error, node, vpn_id) VALUES (@id, @service_type, @response_time_ms, @timestamp, @userid, @error, @node, @vpn_id)",
  'upsert_incident': "INSERT OR UPDATE Incident (id, recordedTimestamp, agentTaskId, issue, strategy, root_cause, resolution, resolvedTimestamp) VALUES (@id, @recordedTimestamp, @agentTaskId, @issue, @strategy, @root_cause, @resolution, @resolvedTimestamp)",
  'upsert_network_metrics': "INSERT OR UPDATE NetworkMetrics (id, kind, name, timestamp, metrics, interface_id) VALUES (@id, @kind, @name, @timestamp, @metrics, @interface_id)",
}

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()
logger = logging.getLogger(__name__)

# ------------------------------------------
# Build a serialized JSON representation of the 
# body that fit into a INSERT/UPDATE SQL statement
#
# **WARNING** Please think twice before making modifications
# here as it took me a lot of trial and errors to come up
# with this solution
# ------------------------------------------
def body_sql_json_dump(string_dump):
  # Double escape the \" sequences created by the santitize call so as to build
  # a syntactically correct SQL INSERT statement for Spanner to execute.
  # Also escape single quotes as single quotes are used to enclose the
  # JSON string in the SQL statement.
  return string_dump.replace('\\n','\\\\n').replace('\\"', '\\\\"').replace("'", "\\'")
 
def body_string_dump(body, kind, namespace, name):
  # Do not rely on the body object from kopf. Get it from
  # K8s directly
  api = kubernetes.client.ApiClient()
  client = kubernetes.dynamic.DynamicClient(api)
  resource_api = get_resource_api(body.get('apiVersion'), kind, client)
  resource = resource_api.get(namespace=namespace, name=name)
  #sanitized_resource = api.sanitize_for_serialization(resource.to_dict())
  #logger.debug("resource: %s",sanitized_resource)

  # Remove some JSON keys that Spanner JSON doesn't like although it is perfectly
  # valid and sanitized (invalid JSON litteral error on SQL INSERT)
  resource_dict = api.sanitize_for_serialization(resource.to_dict())

  resource_dict['metadata'].pop('managedFields', None)
  if 'annotations' in resource_dict['metadata']:
    # CAUTION !! We are iterating through keys that we can possibly delete 
    # so keep the for loop below exactly as is (the call to list() does
    # a copy of the keys)
    for key in list(resource_dict['metadata']['annotations'].keys()):
      if key.startswith('kopf'):
        resource_dict['metadata']['annotations'].pop(key, None)
 
  return json.dumps(resource_dict, ensure_ascii = True)

# ------------------------------------------
# Extract a human readbale status and return a well 
# formatted string to use in SQL INSERT (either NULL or
# "'status_string'")
# ------------------------------------------
def get_status(body):
  status_value = "NULL"
  status = body.get('status')
  if status is not None:
    conditions = status.get('conditions')
    # NOTE: conditions is a list object
    if conditions is not None:
      reason = conditions[0].get('reason')
      if reason is not None:
        status_value = reason
    else:
      if body['kind'].lower() in ['wireguardappliance', 'pointtopointservice', 'meshservice', 'userplanefunction', 'controlplane', 'datanetwork','ueransim']:
        if 'currentStatus' in body['status']:
          status_value = body['status']['currentStatus']
        else:
          svc = body['kind'].lower()
          if (svc in body['status']):
            if ('status' in body['status'][svc]):
              status_value = body['status'][svc]['status']
          elif ('kopf' in body['status']) and ('progress' in body['status']['kopf']) and (svc in body['status']['kopf']['progress']):
            if ('failure' in body['status']['kopf']['progress'][svc]) and (body['status']['kopf']['progress'][svc]['failure'] == True):
              status_value = 'Failed'

  return status_value

# ------------------------------------------
# Idempotent function to create or update a
# KG resource node
# ------------------------------------------
# @throttled
async def create_or_update_kg_resource_description_node(id, body_string):
  success = True
  if await exist_kg_resource_description_node(id):
    success = success & await update_kg_resource_description_node(id, body_string)
  else:
    success = success & await create_kg_resource_description_node(id, body_string)
  return success

# ------------------------------------------
# Does a KG resource node exists
# ------------------------------------------
# @throttled
async def exist_kg_resource_description_node(id):

  tmpl = SQL_TEMPLATES['exist_kg_res_node']
  sql = tmpl.format(id=id)
  logger.debug("SQL: {}".format(sql))

  try:
    with database.snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    success = (results.one_or_none() is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.debug("{} KG resource node exists)".format(id))
  else:
    logger.debug("{} KG resource node doesn't exist)".format(id))
  return success

# ------------------------------------------
# Create K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def create_kg_resource_description_node(id, body_string):

  def sql_create_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['create_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"KG Resource node created id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node creation failed id: {id}")
  return success


# ------------------------------------------
# Update K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def update_kg_resource_description_node(id, body_string):

  def sql_update_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['update_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string

  row_ct = None
  success = True
  try:
    row_ct = database.run_in_transaction(sql_update_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
  
  if success:
    logger.debug(f"KG Resource node updated id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node update failed id: {id} ")
  return success

# ------------------------------------------
# Delete K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def delete_kg_resource_description_node(id):

  def sql_delete_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['delete_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"id": id},
      param_types={"id": spanner.param_types.STRING})
   
  row_ct = None
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"{id} KG Resource node deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node deletion failed id: {id}")
  return success

# ------------------------------------------
# Helper to find PhysicalRouter ID by name
# ------------------------------------------
async def _get_router_id_by_name(name):
  sql = SQL_TEMPLATES['get_router_id_by_name']
  try:
    with database.snapshot() as snapshot:
      results = snapshot.execute_sql(sql, params={'name': name}, param_types={'name': spanner.param_types.STRING})
      # Get first result in case of duplicates (could happen if routers were recreated)
      for row in results:
        return row[0]  # Return the first match
  except Exception as e:
    logger.error(f"Error finding router id for {name}: {e}")
  return None

# ------------------------------------------
# Sync VyOSInfrastructure
# ------------------------------------------
async def sync_vyos_infrastructure(body, spec, name, uid, logger):
    logger.info(f"Syncing VyOSInfrastructure {name}")
    # Sync networks as LogicalSubnets
    networks = spec.get('networks', [])
    for net in networks:
        subnet_id = f"subnet:{net['name']}"
        # Convert to dict if it's a Kubernetes object
        net_dict = dict(net) if hasattr(net, '__iter__') and not isinstance(net, (str, bytes)) else net
        props = json.dumps(net_dict)
        
        def sql_upsert(transaction):
            transaction.execute_update(
                SQL_TEMPLATES['upsert_subnet'],
                params={
                    'id': subnet_id,
                    'cidr': net.get('subnet', ''),
                    'network_type': net.get('network_type', 'unknown'),
                    'description': net.get('description', ''),
                    'properties': props
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'cidr': spanner.param_types.STRING,
                    'network_type': spanner.param_types.STRING,
                    'description': spanner.param_types.STRING,
                    'properties': spanner.param_types.JSON
                }
            )
        try:
            database.run_in_transaction(sql_upsert)
        except Exception as e:
            logger.error(f"Failed to upsert subnet {subnet_id}: {e}")
        
        # Create PhysicalLink if this network has connected_routers (indicates a physical link)
        connected_routers = net.get('connected_routers', [])
        if len(connected_routers) >= 2:
            # This is a physical link connecting routers
            link_id = f"link:{net['name']}"
            link_name = net.get('name', '')
            bandwidth = net.get('bandwidth', 'unknown')
            link_status = 'UP'  # Could be derived from network/router status
            
            # Build link properties including all network details
            link_props = {
                'subnet': net.get('subnet', ''),
                'network_type': net.get('network_type', 'unknown'),
                'vlan': net.get('vlan', None),
                'mtu': net.get('mtu', 1500),
                'description': net.get('description', ''),
                'connected_routers': connected_routers
            }
            
            def sql_upsert_link(transaction):
                transaction.execute_update(
                    SQL_TEMPLATES['upsert_phy_link'],
                    params={
                        'id': link_id,
                        'name': link_name,
                        'bandwidth': bandwidth,
                        'status': link_status,
                        'properties': json.dumps(link_props)
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'name': spanner.param_types.STRING,
                        'bandwidth': spanner.param_types.STRING,
                        'status': spanner.param_types.STRING,
                        'properties': spanner.param_types.JSON
                    }
                )
            
            try:
                database.run_in_transaction(sql_upsert_link)
                logger.info(f"Created PhysicalLink {link_id} connecting {len(connected_routers)} routers")
            except Exception as e:
                logger.error(f"Failed to create PhysicalLink {link_id}: {e}")
            
            # Create Interface_Link associations for each connected router
            for router_conn in connected_routers:
                router_name = router_conn.get('router_name')
                interface_name = router_conn.get('interface')
                
                if not router_name or not interface_name:
                    logger.warning(f"Skipping connection with missing router_name or interface in link {link_id}")
                    continue
                
                # Look up router ID by name
                router_id = await _get_router_id_by_name(router_name)
                
                if not router_id:
                    logger.debug(f"Router {router_name} not found yet for link {link_id}, will sync when router is created")
                    continue
                
                interface_id = f"{router_id}:interface:{interface_name}"
                
                def sql_upsert_iface_link(transaction):
                    transaction.execute_update(
                        SQL_TEMPLATES['upsert_interface_link'],
                        params={
                            'interface_id': interface_id,
                            'link_id': link_id
                        },
                        param_types={
                            'interface_id': spanner.param_types.STRING,
                            'link_id': spanner.param_types.STRING
                        }
                    )
                
                try:
                    database.run_in_transaction(sql_upsert_iface_link)
                    logger.debug(f"Linked interface {interface_id} to {link_id}")
                except Exception as e:
                    logger.error(f"Failed to link interface {interface_id} to {link_id}: {e}")


async def delete_vyos_infrastructure(uid, spec, logger):
    """Delete VyOSInfrastructure and associated subnets and physical links"""
    logger.info(f"Deleting VyOSInfrastructure {uid}")
    
    def sql_delete(transaction):
        # Delete interface-link associations
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE link_id LIKE 'link:%'",
            params={},
            param_types={}
        )
        
        # Delete physical links
        transaction.execute_update(
            "DELETE FROM PhysicalLink WHERE id LIKE 'link:%'",
            params={},
            param_types={}
        )
        
        # Delete subnet associations
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE subnet_id LIKE 'subnet:%'",
            params={},
            param_types={}
        )
        
        # Delete logical subnets created by this infrastructure
        transaction.execute_update(
            "DELETE FROM LogicalSubnet WHERE id LIKE 'subnet:%'",
            params={},
            param_types={}
        )
    
    try:
        database.run_in_transaction(sql_delete)
        logger.info(f"Successfully deleted VyOSInfrastructure topology for {uid}")
    except Exception as e:
        logger.error(f"Failed to delete VyOSInfrastructure {uid}: {e}")

# ------------------------------------------
# Sync PhysicalRouter
# ------------------------------------------
async def sync_physical_router(body, spec, name, uid, logger):
    logger.info(f"Syncing PhysicalRouter {name}")
    
    # 1. Upsert Router
    # Use router name as ID to prevent duplicates when router is recreated
    router_id = f"router:{name}"
    
    # Extract status from CRD status field
    router_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        router_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            router_status = status_str
    
    def sql_upsert_router(transaction):
        # Convert spec to dict if it's a Kubernetes object
        spec_dict = dict(spec) if hasattr(spec, '__iter__') and not isinstance(spec, (str, bytes)) else spec
        
        # Get location from spec.location (VyOS Infrastructure uses latitude/longitude, not lat/lon)
        location = spec.get('location', {})
        metadata_labels = body.get('metadata', {}).get('labels', {})
        
        transaction.execute_update(
            SQL_TEMPLATES['upsert_phy_router'],
            params={
                'id': router_id,
                'name': name,
                'vendor': spec.get('vendor', 'VyOS'),
                'model': spec.get('model', 'Virtual'),
                'location_city': location.get('city') or metadata_labels.get('city', 'Unknown'),
                'location_lat': float(location.get('latitude') or location.get('lat') or metadata_labels.get('latitude') or metadata_labels.get('lat') or 0.0),
                'location_lon': float(location.get('longitude') or location.get('lon') or metadata_labels.get('longitude') or metadata_labels.get('lon') or 0.0),
                'role': 'Router',
                'status': router_status,
                'config': json.dumps(spec_dict)
            },
            param_types={
                'id': spanner.param_types.STRING,
                'name': spanner.param_types.STRING,
                'vendor': spanner.param_types.STRING,
                'model': spanner.param_types.STRING,
                'location_city': spanner.param_types.STRING,
                'location_lat': spanner.param_types.FLOAT64,
                'location_lon': spanner.param_types.FLOAT64,
                'role': spanner.param_types.STRING,
                'status': spanner.param_types.STRING,
                'config': spanner.param_types.JSON
            }
        )
    
    try:
        database.run_in_transaction(sql_upsert_router)
    except Exception as e:
        logger.error(f"Failed to upsert router {name}: {e}")
        return

    # 2. Upsert Interfaces
    interfaces = spec.get('interfaces', [])
    for iface in interfaces:
        # Handle both string and object interface definitions
        if isinstance(iface, str):
            iface_name = iface
            iface_data = {}
        else:
            iface_name = iface.get('name', 'unknown')
            iface_data = iface
        
        iface_id = f"{router_id}:interface:{iface_name}"
        
        # Extract IP address (remove CIDR if present)
        ip_address = iface_data.get('address', '0.0.0.0')
        if '/' in ip_address:
            ip_address = ip_address.split('/')[0]
        
        # Determine interface status from router status or interface specific status
        iface_status = 'Unknown'
        if status_obj and 'interfaces' in status_obj:
            for iface_status_obj in status_obj['interfaces']:
                if iface_status_obj.get('name') == iface_name:
                    iface_status = iface_status_obj.get('status', 'Unknown')
                    break
        
        # If no specific status, use enabled flag or default to UP if router is running
        if iface_status == 'Unknown':
            if iface_data.get('enabled', True):
                iface_status = 'up' if router_status in ['Running', 'Ready'] else 'admin-down'
            else:
                iface_status = 'admin-down'
        
        # Extract speed and media type with better defaults
        speed = iface_data.get('speed', '1G')  # More realistic default
        media_type = iface_data.get('media_type', 'ethernet')
        if iface_name == 'lo':
            media_type = 'loopback'
            speed = 'N/A'
        
        def sql_upsert_iface(transaction):
            transaction.execute_update(
                SQL_TEMPLATES['upsert_phy_interface'],
                params={
                    'id': iface_id,
                    'router_id': router_id,
                    'name': iface_name,
                    'speed': speed,
                    'media_type': media_type,
                    'ip_address': ip_address,
                    'mac_address': iface_data.get('mac', ''),
                    'status': iface_status,
                    'config': json.dumps(iface_data)
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'router_id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'speed': spanner.param_types.STRING,
                    'media_type': spanner.param_types.STRING,
                    'ip_address': spanner.param_types.STRING,
                    'mac_address': spanner.param_types.STRING,
                    'status': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON
                }
            )
        try:
            database.run_in_transaction(sql_upsert_iface)
        except Exception as e:
            logger.error(f"Failed to upsert interface {iface_id}: {e}")
        
        # 3. Create subnet associations for interfaces with IP addresses
        if iface_data.get('address'):
            # Extract CIDR network from interface address
            addr_cidr = iface_data.get('address')
            if '/' in addr_cidr:
                # Create the LogicalSubnet first (required by foreign key constraint)
                subnet_id = f"subnet:{addr_cidr}"
                
                def sql_upsert_subnet(transaction):
                    transaction.execute_update(
                        SQL_TEMPLATES['upsert_subnet'],
                        params={
                            'id': subnet_id,
                            'cidr': addr_cidr,
                            'network_type': 'interface',
                            'description': f'Subnet for interface {iface_name}',
                            'properties': '{}'
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'cidr': spanner.param_types.STRING,
                            'network_type': spanner.param_types.STRING,
                            'description': spanner.param_types.STRING,
                            'properties': spanner.param_types.JSON
                        }
                    )
                try:
                    database.run_in_transaction(sql_upsert_subnet)
                except Exception as e:
                    logger.error(f"Failed to create subnet {subnet_id}: {e}")
                    continue  # Skip association if subnet creation fails
                
                # Now create the association (subnet must exist due to foreign key)
                def sql_upsert_subnet_assoc(transaction):
                    transaction.execute_update(
                        SQL_TEMPLATES['upsert_subnet_assoc'],
                        params={
                            'entity_id': iface_id,
                            'subnet_id': subnet_id,
                            'entity_type': 'Interface'
                        },
                        param_types={
                            'entity_id': spanner.param_types.STRING,
                            'subnet_id': spanner.param_types.STRING,
                            'entity_type': spanner.param_types.STRING
                        }
                    )
                try:
                    database.run_in_transaction(sql_upsert_subnet_assoc)
                except Exception as e:
                    logger.error(f"Failed to create subnet association for {iface_id}: {e}")


async def delete_physical_router(uid, name=None):
    """Delete physical router and cascade delete related entities"""
    # Use name-based ID if available, otherwise fall back to UID for backwards compatibility
    router_id = f"router:{name}" if name else uid
    logger.info(f"Deleting PhysicalRouter {router_id}")
    
    def sql_delete(transaction):
        # Delete subnet associations for all interfaces of this router
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE entity_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # Delete interface-link associations
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE interface_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # Delete interfaces
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_interface_by_router'],
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # Delete router
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_router'],
            params={'id': router_id},
            param_types={'id': spanner.param_types.STRING}
        )
    
    try:
        database.run_in_transaction(sql_delete)
        logger.info(f"Successfully deleted PhysicalRouter {uid}")
    except Exception as e:
        logger.error(f"Failed to delete router {uid}: {e}")

# ------------------------------------------
# Sync L3VPNService
# ------------------------------------------
async def sync_l3vpn_service(body, spec, name, uid, logger):
    logger.info(f"Syncing L3VPNService {name}")
    
    # Extract status from CRD
    l3vpn_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        l3vpn_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            l3vpn_status = status_str
    
    # Track VPN IDs created from this CRD for delete tracking
    vpn_ids_in_crd = []
    
    # 1. Upsert VPN Services
    services = spec.get('services', [])
    for svc in services:
        vpn_id = f"vpn:{svc['name']}"
        vpn_ids_in_crd.append(vpn_id)
        customer_id = "cust:default" # Placeholder for customer
        
        # Ensure customer exists
        def sql_upsert_cust(transaction):
             transaction.execute_update(
                SQL_TEMPLATES['upsert_customer'],
                params={'id': customer_id, 'name': 'Default Customer', 'type': 'Internal', 'properties': '{}'},
                param_types={'id': spanner.param_types.STRING, 'name': spanner.param_types.STRING, 'type': spanner.param_types.STRING, 'properties': spanner.param_types.JSON}
             )
        try:
             database.run_in_transaction(sql_upsert_cust)
        except:
             pass

        def sql_upsert_vpn(transaction):
            transaction.execute_update(
                SQL_TEMPLATES['upsert_l3vpn'],
                params={
                    'id': vpn_id,
                    'customer_id': customer_id,
                    'name': svc['name'],
                    'service_type': svc.get('type', 'l3vpn'),
                    'topology': svc.get('topology', 'any-to-any'),
                    'status': l3vpn_status,
                    'config': json.dumps(dict(svc) if hasattr(svc, '__iter__') and not isinstance(svc, (str, bytes)) else svc)
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'customer_id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'service_type': spanner.param_types.STRING,
                    'topology': spanner.param_types.STRING,
                    'status': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON
                }
            )
        try:
            database.run_in_transaction(sql_upsert_vpn)
        except Exception as e:
            logger.error(f"Failed to upsert VPN {vpn_id}: {e}")
            continue

        # 2. Sync VRFs and BGP from routers
        routers = spec.get('routers', [])
        
        # Get BGP AS number from first router with BGP config (they should all be the same AS)
        local_as = 0
        for r in routers:
            bgp_config = r.get('bgp', {})
            if 'as_number' in bgp_config:
                local_as = bgp_config['as_number']
                break
        
        for r in routers:
            router_name = r['name']
            router_id = await _get_router_id_by_name(router_name)
            if not router_id:
                logger.warning(f"Router {router_name} not found for VPN {svc['name']}, will retry on next sync")
                continue

            # Get router-specific BGP AS if available
            router_bgp_config = r.get('bgp', {})
            router_local_as = router_bgp_config.get('as_number', local_as)

            # VRFs
            vrfs = r.get('vrfs', [])
            for vrf in vrfs:
                vrf_id = f"vrf:{router_name}:{vrf['name']}"
                
                # Determine VRF status - could be UP, DOWN, or configuring
                vrf_status = 'UP' if l3vpn_status in ['Ready', 'Processing'] else 'DOWN'
                
                def sql_upsert_vrf(transaction):
                    transaction.execute_update(
                        SQL_TEMPLATES['upsert_vrf'],
                        params={
                            'id': vrf_id,
                            'router_id': router_id,
                            'vpn_id': vpn_id,
                            'name': vrf['name'],
                            'rd': vrf.get('rd', ''),
                            'status': vrf_status,
                            'config': json.dumps(dict(vrf) if hasattr(vrf, '__iter__') and not isinstance(vrf, (str, bytes)) else vrf)
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'router_id': spanner.param_types.STRING,
                            'vpn_id': spanner.param_types.STRING,
                            'name': spanner.param_types.STRING,
                            'rd': spanner.param_types.STRING,
                            'status': spanner.param_types.STRING,
                            'config': spanner.param_types.JSON
                        }
                    )
                try:
                     database.run_in_transaction(sql_upsert_vrf)
                except Exception as e:
                     logger.error(f"Failed to upsert VRF {vrf_id}: {e}")
                     continue
                
                # Note: VRF-interface association is stored in the VRF config JSON
                # No need for separate Subnet_Association entries since:
                # 1. VRF already has router_id showing which router it's on
                # 2. VRF config JSON contains the list of interfaces
                # 3. Subnet_Association requires subnet_id to reference LogicalSubnet, not interfaces
                
                # BGP Sessions for this VRF
                bgp_vrf_configs = router_bgp_config.get('vrfs', [])
                for bgp_vrf in bgp_vrf_configs:
                    if bgp_vrf['name'] == vrf['name']:
                        neighbors = bgp_vrf.get('neighbors', [])
                        for n in neighbors:
                            peer_ip = n.get('peer', '')
                            bgp_id = f"bgp:{router_name}:{vrf['name']}:{peer_ip}"
                            remote_as = n.get('remote_as', 0)
                            
                            # Determine BGP session status
                            bgp_status = 'Established' if l3vpn_status == 'Ready' else 'Idle'
                            
                            def sql_upsert_bgp(transaction):
                                transaction.execute_update(
                                    SQL_TEMPLATES['upsert_bgp'],
                                    params={
                                        'id': bgp_id,
                                        'vrf_id': vrf_id,
                                        'local_as': router_local_as,
                                        'remote_as': remote_as,
                                        'peer_ip': peer_ip,
                                        'status': bgp_status,
                                        'config': json.dumps(dict(n) if hasattr(n, '__iter__') and not isinstance(n, (str, bytes)) else n)
                                    },
                                    param_types={
                                        'id': spanner.param_types.STRING,
                                        'vrf_id': spanner.param_types.STRING,
                                        'local_as': spanner.param_types.INT64,
                                        'remote_as': spanner.param_types.INT64,
                                        'peer_ip': spanner.param_types.STRING,
                                        'status': spanner.param_types.STRING,
                                        'config': spanner.param_types.JSON
                                    }
                                )
                            try:
                                database.run_in_transaction(sql_upsert_bgp)
                                logger.debug(f"Upserted BGP session {bgp_id} with local_as={router_local_as}")
                            except Exception as e:
                                logger.error(f"Failed to upsert BGP {bgp_id}: {e}")
                                continue
                            
                            # Create BGP peering relationships (bidirectional)
                            # Find matching reverse session
                            reverse_bgp_id = f"bgp:*:{vrf['name']}:{peer_ip}"
                            await _create_bgp_peering(bgp_id, peer_ip, vrf['name'], logger)
    
    # Store VPN IDs in metadata for later cleanup
    return vpn_ids_in_crd


async def delete_l3vpn_service(uid):
    """Delete L3VPN service and cascade delete VRFs and BGP sessions"""
    logger.info(f"Deleting L3VPN Service CRD {uid}")
    
    # We need to find all VPNs that match this CRD's UID pattern
    # Since we don't have the body, we'll delete by pattern matching
    # VPN IDs are created as f"vpn:{svc['name']}" where svc comes from this CRD
    
    def sql_delete(transaction):
        # First, find all VPNs and VRFs to delete BGP sessions
        # Delete BGP sessions for all VRFs associated with VPNs from this CRD
        # Note: This is a broad delete - in production you'd want better tracking
        transaction.execute_update(
            """
            DELETE FROM BGPSession 
            WHERE vrf_id IN (
                SELECT id FROM VRF WHERE vpn_id LIKE 'vpn:%'
            )
            """,
            params={},
            param_types={}
        )
        
        # Delete VRFs associated with VPNs
        transaction.execute_update(
            """
            DELETE FROM VRF WHERE vpn_id LIKE 'vpn:%'
            """,
            params={},
            param_types={}
        )
        
        # Delete the VPN services themselves
        transaction.execute_update(
            """
            DELETE FROM L3VPNService WHERE id LIKE 'vpn:%'
            """,
            params={},
            param_types={}
        )
    
    try:
        database.run_in_transaction(sql_delete)
        logger.info(f"Successfully deleted L3VPN services and related entities for CRD {uid}")
    except Exception as e:
        logger.error(f"Failed to delete L3VPN service {uid}: {e}")


# ------------------------------------------
# Create BGP Peering Relationship
# ------------------------------------------
async def _create_bgp_peering(bgp_session_id, peer_ip, vrf_name, logger):
    """
    Create BGP peering relationship in BGP_Peering table.
    This finds the matching reverse BGP session and creates the peering link.
    """
    # Query to find the reverse BGP session (where local peer IP matches our peer_ip)
    query = """
        SELECT id FROM BGPSession 
        WHERE peer_ip != @peer_ip 
        AND vrf_id LIKE @vrf_pattern
        LIMIT 10
    """
    
    try:
        with database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                query,
                params={
                    'peer_ip': peer_ip,
                    'vrf_pattern': f'%:{vrf_name}'
                },
                param_types={
                    'peer_ip': spanner.param_types.STRING,
                    'vrf_pattern': spanner.param_types.STRING
                }
            )
            
            for row in results:
                peer_bgp_id = row[0]
                
                # Create bidirectional peering entries
                def sql_insert_peering(transaction):
                    # Insert both directions
                    transaction.execute_update(
                        "INSERT OR IGNORE INTO BGP_Peering (session_id_a, session_id_b) VALUES (@id_a, @id_b)",
                        params={'id_a': bgp_session_id, 'id_b': peer_bgp_id},
                        param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
                    )
                    transaction.execute_update(
                        "INSERT OR IGNORE INTO BGP_Peering (session_id_a, session_id_b) VALUES (@id_a, @id_b)",
                        params={'id_a': peer_bgp_id, 'id_b': bgp_session_id},
                        param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
                    )
                
                database.run_in_transaction(sql_insert_peering)
                logger.debug(f"Created BGP peering: {bgp_session_id} <-> {peer_bgp_id}")
                
    except Exception as e:
        logger.debug(f"Could not create BGP peering for {bgp_session_id}: {e}")

