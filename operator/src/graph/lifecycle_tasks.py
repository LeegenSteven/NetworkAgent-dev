import logging
import utils.constants as constants
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner

SQL_TEMPLATES = {
  'create_nw_node': "INSERT NetworkNode (id, kind, name, display_name, node_property)" 
                    " VALUES ('{id}', '{kind}', '{name}', '{display_name}', {crd})",
  'delete_nw_node': "DELETE FROM NetworkNode WHERE id = '{id}'",
  'create_nw_cnx': "INSERT NetworkConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_nw_cnx': "DELETE FROM NetworkConnection WHERE to_id = '{id}'"
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
# Create a network node
# ------------------------------------------
def create_network_node(crd, name, kind, uid):

  def sql_create_network_node(transaction):
    tmpl = SQL_TEMPLATES['create_nw_node']
    display_name="{} ({})".format(kind, name)
    # TODO: Do not insert CRD for now. The serialized JSON uses single quotes
    # which causes the value in INSERT to fail
    sql = tmpl.format(id=uid, kind=kind, name=name, display_name=display_name, crd='NULL')
    logger.info("SQL: {}".format(sql))
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_network_node)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.info("{} node inserted (row count: {})".format(uid,row_ct))
  else:
    logger.error("Node {} creation failed".format(uid))
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
    logger.info("{} -> {} connection inserted (row count: {})".format(parent_uid,uid,row_ct))
  else:
    logger.error("{} -> {} connection creation failed".format(parent_uid, uid))
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
    logger.info("{} connection(s) deleted for node {}".format(row_ct, uid))
  else:
    logger.error("Connection {} deletion failed".format(uid))
  return success
