import logging
import utils.constants as constants
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner

SQL_TEMPLATES = {
  'insert_nw_node': "INSERT NetworkNode (id, kind, name, display_name, node_properties)" 
                    " VALUES ('{id}', '{kind}', '{name}', '{display_name}', '{crd}')",
  'delete_nw_node': "DELETE FROM NetworkNode WHERE id = '{id}'",
  'insert_nw_cnx': "INSERT NetworkConnection (id, to_id) VALUES ('{id}', '{to_id}')",
  'delete_nw_cnx': "DELETE FROM NetworkConnection WHERE id = '{id}'"
}

# Connect to Spanner database
async def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()
logger = logging.getLogger(__name__)

async def create_network_node(crd, name, kind, uid):
  tmpl = SQL_TEMPLATES['insert_nw_node']
  sql = tmpl.format(id=uid, kind=kind, name=name, display_name="{kind} ({name})", crd=json.dumps(crd))
  row_ct = database.run_in_transaction(sql)
  logger.info ("{} node(s) inserted.".format(row_ct))
  return (row_ct == 1)

async def delete_network_node(uid):
  tmpl = SQL_TEMPLATES['delete_nw_node']
  sql = tmpl.format(id=uid)
  row_ct = database.run_in_transaction(sql)
  logger.info ("{} node(s) deleted.".format(row_ct))
  return (row_ct == 1)

async def create_network_connection(uid, parent_uid):
  tmpl = SQL_TEMPLATES['insert_nw_cnx']
  sql = tmpl.format(id=uid, to_id=parent_uid)
  row_ct = database.run_in_transaction(sql)
  logger.info ("{} connection(s) inserted.".format(row_ct))
  return (row_ct == 1)

async def delete_network_connection(uid):
  tmpl = SQL_TEMPLATES['delete_nw_cnx']
  sql = tmpl.format(id=uid)
  row_ct = database.run_in_transaction(sql)
  logger.info ("{} connection(s) deleted.".format(row_ct))
  return (row_ct > 0)