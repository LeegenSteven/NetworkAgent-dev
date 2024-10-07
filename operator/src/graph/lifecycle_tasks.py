import logging
import utils.constants as constants
from utils.compute import *
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner

SQL_TEMPLATES = {
  'create_nw_node': "INSERT NetworkNode (id, kind, name, display_name, self_link, status, node_property)" 
                    " VALUES ('{id}', '{kind}', '{name}', '{display_name}', {self_link}, {status}, JSON '{body}')",
  'delete_nw_node': "DELETE FROM NetworkNode WHERE id = '{id}'",
  'update_nw_node': "UPDATE NetworkNode SET status = {status} WHERE id = '{id}'",
  'create_rs_cnx': "INSERT ResourceConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_rs_cnx': "DELETE FROM ResourceConnection WHERE (id = '{id}' OR to_id = '{id}')",
  'create_nw_cnx': "INSERT NetworkConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_nw_cnx': "DELETE FROM NetworkConnection WHERE (id = '{id}' OR to_id = '{id}')"
}

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()
logger = logging.getLogger(__name__)

# extract the status and return a string well 
# formatted for the SQL INSERT (either NULL or
# "'status_string'")
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
def create_network_node(body, spec, name, kind, uid):

  def sql_create_network_node(transaction):
    tmpl = SQL_TEMPLATES['create_nw_node']
    display_name = f"{kind} ({name})"
    status = get_status(body)
    
    # get the serialized JSON representation of the resource to store
    # in the database
    api = kubernetes.client.ApiClient()
    client = kubernetes.dynamic.DynamicClient(api)
    resource_api = get_resource_api(body.get('apiVersion'), kind, client)
    resource = resource_api.get(namespace="automation", name=name)
    #sanitized_resource = api.sanitize_for_serialization(resource.to_dict())
    #logger.debug("resource: %s",sanitized_resource)

    # Remove some JSON keys that Spanner JSON doesn't like although it is perfectly
    # valid and sanitized (invalid JSON litteral error on SQL INSERT)
    resource_dict = api.sanitize_for_serialization(resource.to_dict())

    resource_dict['metadata'].pop('managedFields', None)
    if 'annotations' in resource_dict['metadata']:
      # CAUTION !! We are iterating through keys that we can posibly delete 
      # so keeps the for loop below exactly as is (the call to list() does
      # a copy of the keys)
      for key in list(resource_dict['metadata']['annotations'].keys()):
        if key.startswith('kopf'):
          resource_dict['metadata']['annotations'].pop(key, None)
    # Double escape the \" sequences created by the santitize call so as to build
    # a syntactically correct SQL INSERT statement for Spanner to execute
    resource_json = json.dumps(resource_dict, ensure_ascii = True).replace('\\n','\\\\n').replace('\\"', '\\\\"')

    # Build and execute the SQL query
    sql = tmpl.format(id=uid, kind=kind, name=name, display_name=display_name, 
                      self_link='NULL', status=status, body=resource_json)
    logger.info(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
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

def update_network_node(body, spec, name, kind, uid):

  def sql_update_network_node(transaction):
    tmpl = SQL_TEMPLATES['update_nw_node']
    sql = tmpl.format(status=status, id=uid)
    logger.info(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  # For now we only update the status field
  status = get_status(body)
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
# Delete network connections
# ------------------------------------------
def delete_network_connection(uid):

  def sql_delete_network_connection(transaction):
    tmpl = SQL_TEMPLATES['delete_nw_cnx']
    sql = tmpl.format(id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_network_connection)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} network connection(s) deleted for node {}".format(row_ct, uid))
  else:
<<<<<<< Updated upstream
    logger.error("Connection {} deletion failed".format(uid))
  return success
=======
    logger.error("Network connection {} deletion failed".format(uid))
  return success

# ------------------------------------------
# Create K8s resource connection
# ------------------------------------------
def create_resource_connection(parent_uid, uid):

  def sql_create_resource_connection(transaction):
    tmpl = SQL_TEMPLATES['create_rs_cnx']
    sql = tmpl.format(id=parent_uid, to_id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_resource_connection)
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
def delete_resource_connection(uid):

  def sql_delete_resource_connection(transaction):
    tmpl = SQL_TEMPLATES['delete_rs_cnx']
    sql = tmpl.format(id=uid)
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_resource_connection)
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
  subnet_name = None
  subnet_entry = spec_base.get(attribute)
  if subnet_entry is not None:
    subnet_name = subnet_entry.get('name')
    if subnet_name is None: 
      subnet_url = subnet_entry.get('external')
      if subnet_url is not None:
        subnet_name = subnet_url.split('/')[-1]
  return subnet_name

# Find the reference network of of K8s resource
# given its spec (or part of its spec) as a parameter
async def find_network_reference(spec_base):
  # Try finding a subnet resource first
  subnet_name = find_xnet_name(spec_base, 'subnetworkRef')
  if subnet_name is not None:
      subnet = await get_subnetwork(subnet_name)
      if subnet is not None:
        logger.info("Found subnet %s", subnet_name)
        return subnet
      
  # Try finding a net resource second
  net_name = find_xnet_name(spec_base, 'networkRef')
  if net_name is not None:
      net = await get_network(net_name)
      if net is not None:
        logger.info("Found net %s", net_name)
        return net
  
  # At that stage we haven't found any net or subnet resource
  return None
>>>>>>> Stashed changes
