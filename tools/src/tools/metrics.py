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
from utils.k8s import get_credentials
import json
from typing import Annotated, Dict, List
from google.cloud import spanner
import utils.globals as globals
from mcp.types import ToolAnnotations
from datetime import datetime
import time

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

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_last_metrics_for_id(
    id: Annotated[str, "id of the ComputeInstance"]
  )->List[Dict]:
  """
  Lookup the most recent metrics for a given ComputeInstance
  
  Args:
    id: string identifier for the ComputeInstance

  Returns:
    Dictionary of metrics representing the network statistics for all network interfaces in the ComputeInstance. Each
    nic is a key in the dictionary
  """
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

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_last_metrics_by_name(
    name: Annotated[str, "name of the ComputeInstance"]
  )->List[Dict]:
  """
  Lookup the most recent metrics for a given ComputeInstance
  
  Args:
    name: name of the ComputeInstance

  Returns:
    Dictionary of metrics representing the network statistics for all network interfaces in the ComputeInstance. Each
    nic is a key in the dictionary
  """
  logger.info(f"fetching last metrics for {name}")
  with database.snapshot() as snapshot:
    try:
      sql = f"""SELECT id, kind, name, timestamp, metrics
        FROM NetworkMetrics 
        WHERE name = '{name}' ORDER BY timestamp DESC LIMIT 1"""
      results = snapshot.execute_sql(sql)
      
      # Convert to a dcitionary with resource uid as key
      last_metrics = {}
      for row in results:
        id, kind, name, timestamp, metrics = row
        if id not in last_metrics: last_metrics[id] = []
        last_metrics[id].append({'kind': kind, 'name': name, 'timestamp': timestamp, 'metrics': metrics})

      logger.info(last_metrics)      
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
    
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_all_metrics():
  """
  Fetch all metrics captured for network service and connectivity services
  Returns:
    List of JSON objects representing each metric
    
  """
  logger.info("fetching all metrics")
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

def _parse_datetime_to_timestamp(datetime_str: str) -> int:
  """
  Convert datetime string to Unix timestamp.
  
  Supports multiple formats:
  - ISO format: '2024-01-15T10:30:00Z' or '2024-01-15T10:30:00'
  - Date only: '2024-01-15' (assumes 00:00:00)
  - Unix timestamp as string: '1705312200'
  
  Args:
    datetime_str: String representation of datetime
    
  Returns:
    Unix timestamp as integer (seconds since epoch)
  """
  try:
    # If it's already a Unix timestamp (all digits), return as int
    if datetime_str.isdigit():
      timestamp = int(datetime_str)
      # Convert milliseconds to seconds if needed (timestamps > year 2100 are likely milliseconds)
      if timestamp > 4102444800:  # Jan 1, 2100
        timestamp = timestamp // 1000
      return timestamp
    
    # Try parsing ISO format with timezone
    if datetime_str.endswith('Z'):
      dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
    elif 'T' in datetime_str:
      # ISO format without timezone (assume UTC)
      dt = datetime.fromisoformat(datetime_str)
    else:
      # Date only format, assume start of day
      dt = datetime.fromisoformat(datetime_str + 'T00:00:00')
    
    # Convert to Unix timestamp
    return int(dt.timestamp())
    
  except (ValueError, AttributeError) as e:
    logger.error(f"Failed to parse datetime string '{datetime_str}': {e}")
    raise ValueError(f"Invalid datetime format: {datetime_str}. Use ISO format (YYYY-MM-DDTHH:MM:SS) or Unix timestamp")

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_metrics_by_time_window(
    start_datetime: Annotated[str, "Start datetime string with the format YYYY-MM-DD HH:MM:SS that is suitable to create a python datetime object"],
    end_datetime: Annotated[str, "End datetime string with the format YYYY-MM-DD HH:MM:SS that is suitable to create a python datetime object"],
    name: Annotated[str, "Name of Network Service"],
) -> List[Dict]:
  """
  Fetch metrics for Network Service within a specified time window.
  
  Args:
    start_datetime: Start of the time window (ISO datetime string, date string, or Unix timestamp)
    end_datetime: End of the time window (ISO datetime string, date string, or Unix timestamp)
    name: Name of network service
    
  Returns:
    Dictionary of metrics representing network statistics for all network interfaces
    in the specified time window
    
  Examples:
    - fetch_metrics_by_time_window("2024-01-15T10:30:00Z", "2024-01-15T11:30:00Z")
    - fetch_metrics_by_time_window("2024-01-15", "2024-01-16")
    - fetch_metrics_by_time_window("1705312200", "1705315800")
  """
  try:
    # Convert datetime strings to Unix timestamps
    start_timestamp = _parse_datetime_to_timestamp(start_datetime)
    end_timestamp = _parse_datetime_to_timestamp(end_datetime)
    
    logger.info(f"Fetching metrics from {start_datetime} ({start_timestamp}) to {end_datetime} ({end_timestamp})")
    
    with database.snapshot() as snapshot:
      try:
        # Build the SQL query with optional filters
        base_sql = """SELECT id, kind, name, timestamp, metrics
          FROM NetworkMetrics 
          WHERE timestamp >= @start_ts AND timestamp <= @end_ts"""
        
        params = {
          'start_ts': start_timestamp,
          'end_ts': end_timestamp
        }
        param_types = {
          'start_ts': spanner.param_types.INT64,
          'end_ts': spanner.param_types.INT64
        }
        
        base_sql += " AND name = @name"
        params['name'] = name
        param_types['name'] = spanner.param_types.STRING
        
        # Order by timestamp descending
        base_sql += " ORDER BY id, timestamp DESC"
        
        results = snapshot.execute_sql(
          base_sql,
          params=params,
          param_types=param_types
        )
        
        # Convert to a dictionary with resource id as key
        time_window_metrics = {}
        for row in results:
          row_id, kind, name, timestamp, metrics = row
          if row_id not in time_window_metrics:
            time_window_metrics[row_id] = []
          time_window_metrics[row_id].append({
            'kind': kind,
            'name': name,
            'timestamp': timestamp,
            'metrics': metrics
          })
        
        logger.info(f"Found {len(time_window_metrics)} ComputeInstances with metrics in time window")
        logger.info(time_window_metrics)
        return time_window_metrics
        
      except Exception as e:
        logger.error(f"Metrics time window SQL error: {e}")
        return {}  # Return empty dict on error
        
  except ValueError as e:
    logger.error(f"DateTime parsing error: {e}")
    return {}  # Return empty dict on datetime parsing error

def clear_network_metrics():
  """
  Clears all records from the NetworkMetrics table.
  
  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  logger.info("clear all metrics")
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
