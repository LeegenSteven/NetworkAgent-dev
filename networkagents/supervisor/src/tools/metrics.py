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
from agent_library import get_credentials
import json
from google.cloud import spanner

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
def spanner_connect():
  credentials, _ = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

def fetch_last_metrics_for_id(id):
  with database.snapshot() as snapshot:
    try:
      sql = f"""SELECT id, kind, name, timestamp, metrics
        FROM NetworkMetrics 
        WHERE id = '{id}' ORDER BY timestamp DESC LIMIT 1"""
      results = snapshot.execute_sql(sql)
      
      # Convert to a dcitionary with resource uid as key
      last_metrics = {}
      for row in results:
        id, kind, name, timestamp, metrics = row
        if id not in last_metrics: last_metrics[id] = []
        last_metrics[id].append({'kind': kind, 'name': name, 'timestamp': timestamp, 'metrics': metrics})
      
      return last_metrics
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return []  # Return empty list on error
    
def fetch_all_metrics_for_id(id):
  with database.snapshot() as snapshot:
    try:
      sql = f"""SELECT id, kind, name, timestamp, metrics
        FROM NetworkMetrics 
        WHERE id = '{id}' ORDER BY timestamp DESC"""
      results = snapshot.execute_sql(sql)
      
      # Convert to a dictionary with resource uid as key
      last_metrics = {}
      for row in results:
        id, kind, name, timestamp, metrics = row
        if id not in last_metrics: last_metrics[id] = []
        last_metrics[id].append({'kind': kind, 'name': name, 'timestamp': timestamp, 'metrics': metrics})

      return last_metrics
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return []  # Return empty list on error



def fetch_all_last_metrics():
  with database.snapshot() as snapshot:
    try:
      sql = """SELECT t1.id AS id, t1.kind AS kind, t1.name AS name, t1.timestamp AS timestamp, t1.metrics AS metrics
      FROM NetworkMetrics AS t1
      INNER JOIN (
        SELECT id, MAX(timestamp) AS max_timestamp
        FROM NetworkMetrics
        GROUP BY id
      ) AS t2 ON t1.id = t2.id AND t1.timestamp = t2.max_timestamp;"""
      results = snapshot.execute_sql(sql)
      
      # Convert to a dictionary with resource uid as key
      last_metrics = {}
      for row in results:
        id, kind, name, timestamp, metrics = row
        if id not in last_metrics: last_metrics[id] = []
        last_metrics[id].append({'kind': kind, 'name': name, 'timestamp': timestamp, 'metrics': metrics})
      
      return last_metrics
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return {}  # Return empty list on error
    
def fetch_all_metrics():
  """
  Fetch all metrics captured for network service and connectivity services
  Returns:
    List of JSON objects representing each metric
    
  """
  with database.snapshot() as snapshot:
    try:
      sql = """SELECT id, kind, name, timestamp, metrics
        FROM NetworkMetrics ORDER BY id, timestamp DESC"""
      results = snapshot.execute_sql(sql)

      # Convert to a dcitionary with resource uid as key
      last_metrics = {}
      for row in results:
        id, kind, name, timestamp, metrics = row
        if id not in last_metrics: last_metrics[id] = []
        last_metrics[id].append({'kind': kind, 'name': name, 'timestamp': timestamp, 'metrics': metrics})
 
      return last_metrics
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return {}  # Return empty list on error

def clear_network_metrics():
  """
  Clears all records from the NetworkMetrics table.
  
  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  try:
    def delete_all(transaction):
      row_count = transaction.execute_update(
        "DELETE FROM NetworkMetrics WHERE 1=1"
      )
      logger.info(f"Deleted {row_count} records from NetworkMetrics table")
      return row_count
      
    row_count = database.run_in_transaction(delete_all)
    logger.info(f"Successfully cleared {row_count} records from NetworkMetrics table")
    return True
  except Exception as e:
    logger.error(f"Failed to clear NetworkMetrics table: {e}")
    return False
