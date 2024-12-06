import logging
import utils.constants as constants
from utils.compute import *
import json
import ipaddress
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner

SQL_TEMPLATES = {
  'create_nw_node': "INSERT NetworkNode (id, kind, name, display_name, self_link, status, node_property)" 
                    " VALUES ('{id}', '{kind}', '{name}', '{display_name}', {self_link}, {status}, JSON '{body}')",
  'delete_nw_node': "DELETE FROM NetworkNode WHERE id = '{id}'",
  'update_nw_node': "UPDATE NetworkNode SET status = {status}, node_property = JSON '{body}' WHERE id = '{id}'",
  'create_rs_cnx': "INSERT ResourceConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_node_rs_cnx': "DELETE FROM ResourceConnection WHERE (id = '{id}' OR to_id = '{id}')",
  'create_nw_cnx': "INSERT NetworkConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_node_nw_cnx': "DELETE FROM NetworkConnection WHERE (id = '{id}' OR to_id = '{id}')",
  'exist_nw_cnx': "SELECT id FROM NetworkConnection WHERE (id = '{id}' AND to_id = '{to_id}')"
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
def body_sql_json_dump(body, kind, namespace, name):
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
  # Double escape the \" sequences created by the santitize call so as to build
  # a syntactically correct SQL INSERT statement for Spanner to execute
  return json.dumps(resource_dict, ensure_ascii = True).replace('\\n','\\\\n').replace('\\"', '\\\\"')


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
      #message = conditions.get('message)')
      #type = conditions.get('type)')
      if reason is not None:
        status_value = f"'{reason}'"
    else:
      wireguard = status.get('wireguard')
      if wireguard is not None:
        status_value = wireguard.get('status')
  return status_value

# ------------------------------------------
# Create a network node
# ------------------------------------------
def create_network_node(body, spec, namespace, name, kind, uid):

  def sql_create_network_node(transaction):
    tmpl = SQL_TEMPLATES['create_nw_node']
    # Build and execute the SQL query
    sql = tmpl.format(id=uid, kind=kind, name=name, display_name=display_name, 
                      self_link='NULL', status=status, body=body_dump)
    logger.info(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  display_name = f"{kind} ({name})"
  status = get_status(body)
  # Build a Spanner compatible JSON dump of Body
  body_dump = body_sql_json_dump(body, kind, namespace, name)

  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_network_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
    raise

  if success:
    logger.info(f"{uid} node inserted (row count: {row_ct})")
  else:
    logger.error(f"Node {uid} creation failed")
  return success


# ------------------------------------------
# Update a network node
# ------------------------------------------
def update_network_node(body, spec, namespace, name, kind, uid):

  def sql_update_network_node(transaction):
    tmpl = SQL_TEMPLATES['update_nw_node']
    sql = tmpl.format(status=status, body=body_dump, id=uid)
    logger.info(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  # For now we only update the status field and node property
  status = get_status(body)
  body_dump = body_sql_json_dump(body, kind, namespace, name)
  if status == "NULL":
    logger.info("Status is NULL. No update performed.")
    return True
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_update_network_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
    raise

  if success:
    logger.info(f"{uid} node updated (row count: {row_ct})")
  else:
    logger.error(f"Node {uid} update failed")
  return success

# ------------------------------------------
# Delete a network node
# ------------------------------------------
def delete_network_node(uid):

  def sql_delete_network_node(transaction):
    tmpl = SQL_TEMPLATES['delete_nw_node']
    sql = tmpl.format(id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_network_node)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} node deleted (row count: {})".format(uid,row_ct))
  else:
    logger.error("Node {} deletion failed".format(uid))
  return success

# ------------------------------------------
# Create a network connection
# ------------------------------------------
def create_network_connection(parent_uid, uid):

  def sql_create_network_connection(transaction):
    tmpl = SQL_TEMPLATES['create_nw_cnx']
    sql = tmpl.format(id=parent_uid, to_id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_network_connection)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} -> {} network connection inserted (row count: {})".format(parent_uid,uid,row_ct))
  else:
    logger.error("{} -> {} network connection creation failed".format(parent_uid, uid))
  return success

# ------------------------------------------
# Does a network connection exists
# ------------------------------------------
def exist_network_connection(parent_uid, uid):

  tmpl = SQL_TEMPLATES['exist_nw_cnx']
  sql = tmpl.format(id=parent_uid, to_id=uid)
  logger.info("SQL: {}".format(sql))

  try:
    with database.snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    success = (results.one_or_none() is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} -> {} network connection exists)".format(parent_uid,uid))
  else:
    logger.info("{} -> {} network connection doesn't exist)".format(parent_uid, uid))
  return success

# ------------------------------------------
# Delete network connections
# ------------------------------------------
def delete_node_network_connections(uid):

  def sql_delete_node_network_connections(transaction):
    tmpl = SQL_TEMPLATES['delete_node_nw_cnx']
    sql = tmpl.format(id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_node_network_connections)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} network connection(s) deleted for node {}".format(row_ct, uid))
  else:
    logger.error("Network connection {} deletion failed".format(uid))
  return success

# ------------------------------------------
# Create K8s resource connection
# ------------------------------------------
def create_resource_connection(parent_uid, uid):

  def sql_create_resource_connections(transaction):
    tmpl = SQL_TEMPLATES['create_rs_cnx']
    sql = tmpl.format(id=parent_uid, to_id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_resource_connections)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} -> {} resource connection inserted (row count: {})".format(parent_uid,uid,row_ct))
  else:
    logger.error("{} -> {} resource connection creation failed".format(parent_uid, uid))
  return success

# ------------------------------------------
# Delete K8s resource connections
# ------------------------------------------
def delete_node_resource_connections(uid):

  def sql_delete_node_resource_connection(transaction):
    tmpl = SQL_TEMPLATES['delete_node_rs_cnx']
    sql = tmpl.format(id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_node_resource_connection)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} resource connection(s) deleted for node {}".format(row_ct, uid))
  else:
    logger.error("Resource connection {} deletion failed".format(uid))
  return success

# ------------------------------------------
# Find a network or subnetwork reference
# ------------------------------------------

# Find the (sub)network name either under
# the name or external attirbutes
def find_xnet_name(spec_base, attribute):
  xnet_name = None
  xnet_namespace = None
  xnet_entry = spec_base.get(attribute)
  if xnet_entry is not None:
    xnet_name = xnet_entry.get('name')
    xnet_namespace = xnet_entry.get('namespace')
  return xnet_name, xnet_namespace

# Find the reference network of of K8s resource
# given its spec (or part of its spec) as a parameter
async def find_network_reference(namespace, spec_base):
  # Try finding a subnet resource first
  subnet_name, subnet_namespace = find_xnet_name(spec_base, 'subnetworkRef')
  if not subnet_namespace:
    subnet_namespace = namespace
  if subnet_name is not None:
      subnet = await get_subnetwork(subnet_namespace, subnet_name)
      if subnet is not None:
        logger.info("Found subnet %s in ns %s", subnet_name, subnet_namespace)
        return subnet
      
  # Try finding a net resource second
  net_name,net_namespace = find_xnet_name(spec_base, 'networkRef')
  if not net_namespace:
    net_namespace = namespace
  if net_name is not None:
      try:
        net = await get_network(net_namespace, net_name)
        if net is not None:
          logger.info("Found net %s in ns %s", net_name, net_namespace)
          return net
      except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
          logger.debug("%s in namespace %s not found", net_name, net_namespace)
        else:
          logger.debug(e)    
  # At that stage we haven't found any net or subnet resource
  return None

# Find the route which nextHopIP matches the given destination
# range
async def find_destination_route(dest_range):
  # Loop through route objects and find all matching
  api = kubernetes.client.ApiClient()
  client = kubernetes.dynamic.DynamicClient(api)
  resource_api = get_resource_api(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeRoute")
  routes = resource_api.get().items

  # Select those routes for which the next hop ip matches
  # the destination network range
  network = ipaddress.ip_network(dest_range)
  matching_routes = []
  for r in routes:
    next_hop_ip = r['spec']['nextHopIp']
    if next_hop_ip and (next_hop_ip in network):
      matching_routes.append(r)

  return matching_routes


