import logging
from utils.compute import *
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner
# This is to generate KG node embeddings
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

# Parameters for vertex AI embedding
TASK_TYPE = "QUESTION_ANSWERING"
ANSWER_TASK_TYPE="RETRIEVAL_DOCUMENT"
EMBEDDING_MODEL_NAME="text-embedding-005"
EMBEDDING_MODEL = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

SQL_TEMPLATES = {
  'create_nw_node': "INSERT NetworkNode (id, kind, name, display_name, self_link, status, node_property)" 
                    " VALUES ('{id}', '{kind}', '{name}', '{display_name}', {self_link}, {status}, JSON '{body}')",
  'delete_nw_node': "DELETE FROM NetworkNode WHERE id = '{id}'",
  'update_nw_node': "UPDATE NetworkNode SET status = {status}, node_property = JSON '{body}' WHERE id = '{id}'",
  'create_rs_cnx': "INSERT ResourceConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_node_rs_cnx': "DELETE FROM ResourceConnection WHERE (id = '{id}' OR to_id = '{id}')",
  'create_nw_cnx': "INSERT NetworkConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_node_nw_cnx': "DELETE FROM NetworkConnection WHERE (id = '{id}' OR to_id = '{id}')",
  'exist_nw_cnx': "SELECT id FROM NetworkConnection WHERE (id = '{id}' AND to_id = '{to_id}')",
  'create_kg_res_node': "INSERT KgResourceDescriptionNode (id, content, embedding)"
                        " VALUES (@id, @content, @embedding)",
  'update_kg_res_node': "UPDATE KgResourceDescriptionNode SET content = @content, embedding = @embedding WHERE id = @id",
  'delete_kg_res_node': "DELETE FROM KgResourceDescriptionNode WHERE id = @id",
  'exist_kg_res_node' : "SELECT id FROM KgResourceDescriptionNode WHERE id = '{id}'"
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
      #message = conditions.get('message)')
      #type = conditions.get('type)')
      if reason is not None:
        status_value = reason
    else:
      wireguard = status.get('wireguard')
      if wireguard is not None:
        status_value = wireguard.get('status')
      elif body['kind'].lower() in ['pointtopointservice', 'meshservice']:
        if 'currentStatus' in body['status']:
          status_value = body['status']['currentStatus']
        else:
          svc = body['kind'].lower()
          if (svc in body['status']) and ('status' in body['status'][svc]):
            status_value = body['status'][svc]['status']

  return status_value

# ------------------------------------------
# Given a piece of text return the embedding (Array of Float64)
# ------------------------------------------
def get_embedding(text, task_type, model):
  try:
    text_embedding_input = TextEmbeddingInput(task_type=task_type, text=text)
    embeddings = model.get_embeddings([text_embedding_input])
    return embeddings[0].values
  except Exception as e:
    logger.error(f"Embedding error: {e}")
    return []

# ------------------------------------------
# Create a network node
# ------------------------------------------
def create_network_node(body, spec, namespace, name, kind, uid):

  def sql_create_network_node(transaction):
    tmpl = SQL_TEMPLATES['create_nw_node']
    # Build and execute the SQL query
    sql = tmpl.format(id=uid, kind=kind, name=name, display_name=display_name, 
                      self_link='NULL', status=status, body=body_dump)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  display_name = f"{kind} ({name})"
  status = get_status(body)
  if status != 'NULL': status = f"'{status}'"
  # Build a Spanner compatible JSON dump of Body
  body_string = body_string_dump(body, kind, namespace, name)
  body_dump = body_sql_json_dump(body_string)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_network_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  success &= create_or_update_kg_resource_description_node(uid, body_string)

  if success:
    logger.info(f"Network node inserted id: {uid}, kind: {kind}, name: {name} (row count: {row_ct})")
  else:
    logger.error(f"Network node creation failed id: {uid}, kind: {kind}, name: {name}")
  return success


# ------------------------------------------
# Update a network node
# ------------------------------------------
def update_network_node(body, spec, namespace, name, kind, uid):

  def sql_update_network_node(transaction):
    tmpl = SQL_TEMPLATES['update_nw_node']
    sql = tmpl.format(status=status, body=body_dump, id=uid)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  # For now we only update the status field and node property
  status = get_status(body)
  if status != 'NULL': status = f"'{status}'"
  body_string = body_string_dump(body, kind, namespace, name)
  body_dump = body_sql_json_dump(body_string)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_update_network_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  success &= create_or_update_kg_resource_description_node(uid, body_string)

  if success:
    logger.info(f"Network node updated id: {uid}, kind: {kind}, name: {name} (row count: {row_ct})")
  else:
    logger.error(f"Network node update failed id: {uid}, kind: {kind}, name: {name}")
  return success

# ------------------------------------------
# Delete a network node
# ------------------------------------------
def delete_network_node(uid, kind, name):

  def sql_delete_network_node(transaction):
    tmpl = SQL_TEMPLATES['delete_nw_node']
    sql = tmpl.format(id=uid)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_network_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  # Delete related Knowledge Graph node
  success &= delete_kg_resource_description_node(uid)

  if success:
    logger.info(f"Network node deleted id: {uid}, kind: {kind}, name: {name} (row count: {row_ct})")
  else:
    logger.error(f"Network node deletion failed id: {uid}, kind: {kind}, name: {name}")
  return success

# ------------------------------------------
# Create a network connection
# ------------------------------------------
def create_network_connection(parent_uid, uid):

  def sql_create_network_connection(transaction):
    tmpl = SQL_TEMPLATES['create_nw_cnx']
    sql = tmpl.format(id=parent_uid, to_id=uid)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_network_connection)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info("Network node connection from id: {} to id: {} created (row count: {})".format(parent_uid,uid,row_ct))
  else:
    logger.error("Network node connection from id: {} to id: {} creation failed".format(parent_uid, uid))
  return success

# ------------------------------------------
# Does a network connection exists
# ------------------------------------------
def exist_network_connection(parent_uid, uid):

  tmpl = SQL_TEMPLATES['exist_nw_cnx']
  sql = tmpl.format(id=parent_uid, to_id=uid)
  logger.debug("SQL: {}".format(sql))

  try:
    with database.snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    success = (results.one_or_none() is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.debug("Network node connection from id: {} to id: {} exists".format(parent_uid,uid))
  else:
    logger.debug("Network node connection from id: {} to id: {} doesn't exist".format(parent_uid, uid))
  return success

# ------------------------------------------
# Delete network connections
# ------------------------------------------
def delete_node_network_connections(uid):

  def sql_delete_node_network_connections(transaction):
    tmpl = SQL_TEMPLATES['delete_node_nw_cnx']
    sql = tmpl.format(id=uid)
    logger.debug("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_node_network_connections)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info(f"{row_ct} Network node connection(s) deleted for node {uid}")
  else:
    logger.error(f"Network node connection(s) {uid} deletion failed")
  return success

# ------------------------------------------
# Create K8s resource connection
# ------------------------------------------
def create_resource_connection(parent_uid, uid):

  def sql_create_resource_connections(transaction):
    tmpl = SQL_TEMPLATES['create_rs_cnx']
    sql = tmpl.format(id=parent_uid, to_id=uid)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_resource_connections)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug("Resource node connection from id: {} to id: {} created (row count: {})".format(parent_uid,uid,row_ct))
  else:
    logger.debug("Resource node connection from id: {} to id: {} creation failed".format(parent_uid, uid))

  return success

# ------------------------------------------
# Delete K8s resource connections
# ------------------------------------------
def delete_node_resource_connections(uid, kind, name):

  def sql_delete_node_resource_connection(transaction):
    tmpl = SQL_TEMPLATES['delete_node_rs_cnx']
    sql = tmpl.format(id=uid)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_node_resource_connection)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info(f"{row_ct} resource node connection(s) deleted for node id; {uid}, kind: {kind}, name: {name}")
  else:
    logger.error(f"Resource connection for node id: {uid} deletion failed")
  return success

# ------------------------------------------
# Idempotent function to create or update a
# KG resource node
# ------------------------------------------
def create_or_update_kg_resource_description_node(id, body_string):
  success = True
  if exist_kg_resource_description_node(id):
    success &= update_kg_resource_description_node(id, body_string)
  else:
    success &= create_kg_resource_description_node(id, body_string)
  return success

# ------------------------------------------
# Does a KG resource node exists
# ------------------------------------------
def exist_kg_resource_description_node(id):

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
def create_kg_resource_description_node(id, body_string):

  def sql_create_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['create_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "embedding": embedding, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "embedding": spanner.param_types.Array(spanner.param_types.FLOAT64),
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string
  embedding = get_embedding(body_string, TASK_TYPE, EMBEDDING_MODEL)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info(f"KG Resource node created id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node creation failed id: {id}")
  return success


# ------------------------------------------
# Update K8s resource descriptions in Knowledge Graph
# ------------------------------------------
def update_kg_resource_description_node(id, body_string):

  def sql_update_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['update_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "embedding": embedding, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "embedding": spanner.param_types.Array(spanner.param_types.FLOAT64),
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string
  embedding = get_embedding(body_string, TASK_TYPE, EMBEDDING_MODEL)
  logger.debug(f"Embedding for node id {id}")
  logger.debug(f"--> type: {type(body_string)}, body: {body_string}")
  logger.debug(f"--> embedding: {embedding}")

  row_ct = None
  success = True
  try:
    row_ct = database.run_in_transaction(sql_update_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
  
  if success:
    logger.info(f"KG Resource node updated id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node update failed id: {id} ")
  return success

# ------------------------------------------
# Update K8s resource descriptions in Knowledge Graph
# ------------------------------------------
def delete_kg_resource_description_node(id):

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
    logger.info(f"{id} KG Resource node deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node deletion failed id: {id}")
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
        logger.debug("Found subnet %s in ns %s", subnet_name, subnet_namespace)
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
          logger.error("%s in namespace %s not found", net_name, net_namespace)
        else:
          logger.error(e)    
  # At that stage we haven't found any net or subnet resource
  return None

# Find the routes which nextHopIP matches the given destination
# range
async def find_destination_subnets(dest_range):
  # Find all route resources
  resource_api = get_resource_api(
    api_version="compute.cnrm.cloud.google.com/v1beta1", 
    kind="ComputeSubnetwork")
  subnets = resource_api.get().items

  matching_subnets = []
  for r in subnets:
    if r['spec']['ipCidrRange'] == dest_range:
      matching_subnets.append(r)
      logger.info(f"Matching Subnet {r['metadata']['name']} found for route destination range {dest_range}")

  return matching_subnets


# ------------------------------------------
# Idempotent function to create or update the
# network connections of a resource
# ------------------------------------------
async def create_or_update_network_connections(body, spec, meta, uid, namespace, name):  # For all resources look for networkRef and subNetworkRef attributes in spec
  success = True
  # For ComputeInstances look for those fields in the list of NICs
  # under spec/networkInterface 
  if body['kind'] == 'ComputeInstance':
    specs = spec['networkInterface'] or []
  else:
    specs = [spec]
  
  for s in specs:
    logger.debug("Looking for (sub)network ref in %s / %s", body.get('kind'), name)
    xnet = await find_network_reference(namespace, s)
    if xnet:
      xnet_uid = xnet['metadata']['uid']
      if not exist_network_connection(uid, xnet_uid):
        success &= create_network_connection(uid,xnet_uid)


    # Special case for Routes. Find its peer destination route
    # in addition to its network ref (see above)
    if body['kind'] == 'ComputeRoute':
      dest_subnets = await find_destination_subnets(spec['destRange'])
      for ds in dest_subnets:
        dest_subnet_uid = ds['metadata']['uid']
        if not exist_network_connection(uid, dest_subnet_uid):
          success |= create_network_connection(uid, dest_subnet_uid)
  return success