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
from typing import Annotated, Dict, List, Optional
from google.cloud import spanner
import utils.globals as globals
from mcp.types import ToolAnnotations
from datetime import datetime
import time
import statistics

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

######################################################################
# fetch_last_metrics_for_id
######################################################################
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

######################################################################
# fetch_last_metrics_by_name
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_last_metrics_by_name(
    name: Annotated[str, "name of the Network Service or ComputeInstance"]
  )->dict:
  """
  Lookup the most recent metrics for a given ComputeInstance or Network Service by name.
  
  This function retrieves the latest metric entry for a specific ComputeInstance/Network Service
  identified by its name. Returns the most recent timestamp entry for that named resource.
  
  Args:
    name: Name of the ComputeInstance or Network Service to look up

  Returns:
    Dict[str, List[Dict]]: Dictionary with ComputeInstance ID as key and list containing 
    the most recent metric data as value. Each metric entry contains:
    - kind: Type of resource (e.g., 'ComputeInstance')
    - name: Name of the network service/ComputeInstance  
    - timestamp: Unix timestamp of when metrics were collected
    - metrics: JSON object containing the actual metric data (CPU, network interfaces)
    
  Example:
    {
      "compute-123": [
        {
          "kind": "ComputeInstance",
          "name": "upf1",
          "timestamp": 1705312200, 
          "metrics": {
            "hostname": "upf1",
            "cpu": {"cpu_percent": 25.5},
            "interfaces": {
              "eth0": {"byte_sent": 1000, "byte_recv": 2000, ...}
            }
          }
        }
      ]
    }
    
  Note:
    Returns empty dictionary {} if no metrics found for the specified name.
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

######################################################################
# fetch_all_last_metrics
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_all_last_metrics()->dict:
  """
  Fetch the most recent metrics for all ComputeInstances in the system.
  
  This function retrieves the latest metric entry for each ComputeInstance by using
  the maximum timestamp for each unique id. Useful for getting a current snapshot
  of all network services and their performance metrics.
  
  Returns:
    Dict[str, List[Dict]]: Dictionary with ComputeInstance IDs as keys and lists 
    containing the most recent metric data as values. Each metric entry contains:
    - kind: Type of resource (e.g., 'ComputeInstance')
    - name: Name of the network service/ComputeInstance
    - timestamp: Unix timestamp of when metrics were collected
    - metrics: JSON object containing the actual metric data (CPU, network interfaces)
    
  Example:
    {
      "compute-123": [
        {
          "kind": "ComputeInstance",
          "name": "upf1", 
          "timestamp": 1705312200,
          "metrics": {
            "hostname": "upf1",
            "cpu": {"cpu_percent": 25.5},
            "interfaces": {
              "eth0": {"byte_sent": 1000, "byte_recv": 2000, ...}
            }
          }
        }
      ],
      "compute-456": [...]
    }
  """
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

######################################################################
# fetch_all_metrics
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_all_metrics():
  """
  Fetch all metrics captured for network services and connectivity services.
  
  This function retrieves the complete historical record of all metrics for every 
  ComputeInstance in the system. The data is ordered by ComputeInstance ID and 
  timestamp (most recent first). Use this function for comprehensive analysis,
  reporting, or when you need the full metrics history.
  
  Returns:
    Dict[str, List[Dict]]: Dictionary with ComputeInstance IDs as keys and lists 
    containing all metric entries as values. Each metric entry contains:
    - kind: Type of resource (e.g., 'ComputeInstance')
    - name: Name of the network service/ComputeInstance
    - timestamp: Unix timestamp of when metrics were collected
    - metrics: JSON object containing the actual metric data (CPU, network interfaces)
    
  Example:
    {
      "compute-123": [
        {
          "kind": "ComputeInstance",
          "name": "upf1",
          "timestamp": 1705312800,  # Most recent
          "metrics": {
            "hostname": "upf1",
            "cpu": {"cpu_percent": 25.5},
            "interfaces": {
              "eth0": {"byte_sent": 1000, "byte_recv": 2000, ...}
            }
          }
        },
        {
          "kind": "ComputeInstance", 
          "name": "upf1",
          "timestamp": 1705312200,  # Older entry
          "metrics": {...}
        }
      ],
      "compute-456": [...]
    }
    
  Warning:
    This function can return large amounts of data. For recent metrics only,
    consider using fetch_all_last_metrics() instead.
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

######################################################################
# fetch_metrics_by_time_window
######################################################################
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

def _get_aggregation_method(metric_name: str) -> str:
  """
  Determine the appropriate aggregation method for a given metric.
  
  Args:
    metric_name: Name of the metric
    
  Returns:
    Aggregation method: 'average', 'sum', or 'latest'
  """
  if metric_name.endswith('_throughput') or metric_name == 'cpu_percent':
    return 'average'
  elif metric_name.endswith('_delta'):
    return 'sum'
  else:  # Total counters like byte_sent, byte_recv
    return 'latest'

def _aggregate_values(values: List[float], method: str) -> float:
  """
  Aggregate a list of values using the specified method.
  
  Args:
    values: List of numeric values to aggregate
    method: Aggregation method ('average', 'sum', 'latest')
    
  Returns:
    Aggregated value
  """
  if not values:
    return 0.0
    
  if method == 'average':
    return statistics.mean(values)
  elif method == 'sum':
    return sum(values)
  elif method == 'latest':
    return values[-1]  # Most recent value
  else:
    return values[0]

def _flatten_metrics_data(raw_data: Dict, interfaces: Optional[List[str]], 
                         metric_counters: Optional[List[str]]) -> List[Dict]:
  """
  Convert nested metrics data to flat records with filtering.
  
  Args:
    raw_data: Raw metrics data from Spanner
    interfaces: Optional interface filter list
    metric_counters: Optional metric counter filter list
    
  Returns:
    List of flattened metric records
  """
  flattened_records = []
  
  # Determine which interfaces to include
  include_all_interfaces = (interfaces is None or 
                           interfaces == ["all"] or 
                           "all" in interfaces)
  
  # Determine which metrics to include
  available_metrics = ["byte_sent", "byte_recv", "byte_sent_delta", "byte_recv_delta", 
                      "byte_sent_throughput", "byte_recv_throughput", "cpu_percent"]
  include_all_metrics = metric_counters is None
  
  if not include_all_metrics:
    # Validate requested metrics
    invalid_metrics = set(metric_counters) - set(available_metrics)
    if invalid_metrics:
      logger.warning(f"Ignoring invalid metric counters: {invalid_metrics}")
    metric_counters = [m for m in metric_counters if m in available_metrics]
  else:
    metric_counters = available_metrics
  
  for compute_id, entries in raw_data.items():
    for entry in entries:
      timestamp = entry['timestamp']
      hostname = entry['name']
      metrics_json = entry['metrics']
      
      if isinstance(metrics_json, str):
        try:
          metrics = json.loads(metrics_json)
        except json.JSONDecodeError:
          logger.error(f"Failed to parse metrics JSON for {hostname} at {timestamp}")
          continue
      else:
        metrics = metrics_json
      
      # Process CPU metrics
      if 'cpu_percent' in metric_counters and 'cpu' in metrics:
        cpu_value = metrics['cpu'].get('cpu_percent')
        if cpu_value is not None:
          flattened_records.append({
            'timestamp': timestamp,
            'hostname': hostname,
            'interface': 'cpu',
            'metric_name': 'cpu_percent',
            'value': cpu_value
          })
      
      # Process interface metrics
      if 'interfaces' in metrics:
        for iface_name, iface_data in metrics['interfaces'].items():
          # Apply interface filtering
          if not include_all_interfaces and iface_name not in interfaces:
            continue
            
          # Extract requested metrics for this interface
          for metric_name in metric_counters:
            if metric_name != 'cpu_percent' and metric_name in iface_data:
              flattened_records.append({
                'timestamp': timestamp,
                'hostname': hostname,
                'interface': iface_name,
                'metric_name': metric_name,
                'value': iface_data[metric_name]
              })
  
  return flattened_records

def _aggregate_by_sampling_interval(records: List[Dict], 
                                   sampling_interval_seconds: int) -> List[Dict]:
  """
  Aggregate flattened records by sampling intervals.
  
  Args:
    records: List of flattened metric records
    sampling_interval_seconds: Sampling interval in seconds
    
  Returns:
    List of aggregated metric records
  """
  if not records:
    return []
    
  # Group records by (hostname, interface, metric_name, time_bucket)
  grouped = {}
  
  for record in records:
    timestamp = record['timestamp']
    # Calculate time bucket (round down to nearest interval)
    bucket_timestamp = (timestamp // sampling_interval_seconds) * sampling_interval_seconds
    
    key = (
      record['hostname'],
      record['interface'], 
      record['metric_name'],
      bucket_timestamp
    )
    
    if key not in grouped:
      grouped[key] = []
    grouped[key].append(record['value'])
  
  # Aggregate each group
  aggregated_records = []
  for (hostname, interface, metric_name, bucket_timestamp), values in grouped.items():
    aggregation_method = _get_aggregation_method(metric_name)
    aggregated_value = _aggregate_values(values, aggregation_method)
    
    aggregated_records.append({
      'timestamp': bucket_timestamp,
      'hostname': hostname,
      'interface': interface,
      'metric_name': metric_name,
      'value': aggregated_value,
      'aggregation_method': aggregation_method,
      'sample_count': len(values)
    })
  
  # Sort by timestamp, hostname, interface, metric_name
  aggregated_records.sort(key=lambda x: (x['timestamp'], x['hostname'], 
                                        x['interface'], x['metric_name']))
  
  return aggregated_records

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_metrics_by_time_window(
    start_datetime: Annotated[str, "Start datetime string with the format YYYY-MM-DDTHH:MM:SS"],
    end_datetime: Annotated[str, "End datetime string with the format YYYY-MM-DDTHH:MM:SS"],
    name: Annotated[str, "Name of ComputeInstance or Network Service"],
    interfaces: Annotated[Optional[List[str]], "List of interface names to include, or ['all'] for all interfaces"] = None,
    metric_counters: Annotated[Optional[List[str]], "List of metric counters to include"] = None,
    sampling_interval_seconds: Annotated[Optional[int], "Sampling interval in seconds for aggregation"] = None,
) -> List[Dict]:
  """
  Fetch metrics for Network Service within a specified time window with filtering and aggregation options.
  
  Args:
    start_datetime: Start of the time window (YYYY-MM-DDTHH:MM:SS format)
    end_datetime: End of the time window (YYYY-MM-DDTHH:MM:SS format)  
    name: Name of network service
    interfaces: Optional list of interface names to filter by. Use ["all"] or None for all interfaces.
    metric_counters: Optional list of metric types to include. Available: 
                    ["byte_sent", "byte_recv", "byte_sent_delta", "byte_recv_delta", 
                     "byte_sent_throughput", "byte_recv_throughput", "cpu_percent"]
    sampling_interval_seconds: Optional sampling interval for time-based aggregation.
  
  Returns:
    List of dictionaries with flattened metric records.
    
  Examples:
    
    # Example 1: Basic usage - all metrics, all interfaces
    fetch_metrics_by_time_window(
        "2024-01-15T10:30:00", 
        "2024-01-15T11:30:00", 
        "upf1"
    )
    # Returns:
    [
        {
            "timestamp": 1705312200,
            "hostname": "upf1",
            "interface": "eth0", 
            "metric_name": "byte_sent_throughput",
            "value": 1000.5
        },
        {
            "timestamp": 1705312200,
            "hostname": "upf1",
            "interface": "eth0",
            "metric_name": "byte_recv_throughput", 
            "value": 800.2
        },
        {
            "timestamp": 1705312200,
            "hostname": "upf1",
            "interface": "cpu",
            "metric_name": "cpu_percent",
            "value": 25.5
        }
    ]
    
    # Example 2: Filter specific interface and metrics
    fetch_metrics_by_time_window(
        "2024-01-15T10:30:00",
        "2024-01-15T11:30:00", 
        "upf1",
        interfaces=["eth0"],
        metric_counters=["byte_sent_throughput", "byte_recv_throughput"]
    )
    # Returns:
    [
        {
            "timestamp": 1705312200,
            "hostname": "upf1", 
            "interface": "eth0",
            "metric_name": "byte_sent_throughput",
            "value": 1000.5
        },
        {
            "timestamp": 1705312200,
            "hostname": "upf1",
            "interface": "eth0", 
            "metric_name": "byte_recv_throughput",
            "value": 800.2
        }
    ]
    
    # Example 3: With sampling interval (5-minute aggregation)
    fetch_metrics_by_time_window(
        "2024-01-15T10:00:00",
        "2024-01-15T11:00:00",
        "upf1", 
        interfaces=["eth0"],
        metric_counters=["byte_sent_throughput"],
        sampling_interval_seconds=300
    )
    # Returns:
    [
        {
            "timestamp": 1705312200,  # 10:30:00 bucket 
            "hostname": "upf1",
            "interface": "eth0",
            "metric_name": "byte_sent_throughput",
            "value": 950.3,  # averaged over 5-minute window
            "aggregation_method": "average",
            "sample_count": 60  # 60 samples in 5 minutes (5s intervals)
        },
        {
            "timestamp": 1705312500,  # 10:35:00 bucket
            "hostname": "upf1", 
            "interface": "eth0",
            "metric_name": "byte_sent_throughput",
            "value": 1100.7,
            "aggregation_method": "average", 
            "sample_count": 60
        }
    ]
    
    # Example 4: CPU metrics only
    fetch_metrics_by_time_window(
        "2024-01-15T10:30:00",
        "2024-01-15T11:30:00",
        "upf1",
        metric_counters=["cpu_percent"]
    )
    # Returns:
    [
        {
            "timestamp": 1705312200,
            "hostname": "upf1",
            "interface": "cpu",  # Special interface name for CPU metrics
            "metric_name": "cpu_percent", 
            "value": 25.5
        }
    ]
  """
  try:
    # Convert datetime strings to Unix timestamps
    start_timestamp = _parse_datetime_to_timestamp(start_datetime)
    end_timestamp = _parse_datetime_to_timestamp(end_datetime)
    
    logger.info(f"Fetching metrics from {start_datetime} ({start_timestamp}) to {end_datetime} ({end_timestamp})")
    logger.info(f"Filters - interfaces: {interfaces}, metric_counters: {metric_counters}, sampling_interval: {sampling_interval_seconds}")
    
    with database.snapshot() as snapshot:
      try:
        # Build the SQL query with filters
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
        
        # Order by timestamp ascending for proper processing
        base_sql += " ORDER BY id, timestamp ASC"
        logger.info(base_sql)
        logger.info(params)
        
        results = snapshot.execute_sql(
          base_sql,
          params=params,
          param_types=param_types
        )
        
        # Convert to a dictionary with resource id as key
        raw_metrics = {}
        for row in results:
          row_id, kind, name, timestamp, metrics = row
          if row_id not in raw_metrics:
            raw_metrics[row_id] = []
          raw_metrics[row_id].append({
            'kind': kind,
            'name': name,
            'timestamp': timestamp,
            'metrics': metrics
          })
        
        logger.info(f"Found {len(raw_metrics)} ComputeInstances with metrics in time window")
        
        # Step 1: Flatten the metrics data with filtering
        flattened_records = _flatten_metrics_data(raw_metrics, interfaces, metric_counters)
        logger.info(f"Flattened to {len(flattened_records)} metric records")
        
        # Step 2: Apply sampling interval aggregation if requested
        if sampling_interval_seconds is not None and sampling_interval_seconds > 0:
          final_records = _aggregate_by_sampling_interval(flattened_records, sampling_interval_seconds)
          logger.info(f"Aggregated to {len(final_records)} records using {sampling_interval_seconds}s intervals")
        else:
          # Sort by timestamp for consistent output
          final_records = sorted(flattened_records, 
                               key=lambda x: (x['timestamp'], x['hostname'], 
                                            x['interface'], x['metric_name']))
        
        logger.info(f"Returning {len(final_records)} final metric records")
        return final_records
        
      except Exception as e:
        logger.error(f"Metrics time window SQL error: {e}")
        return []  # Return empty list on error
        
  except ValueError as e:
    logger.error(f"DateTime parsing error: {e}")
    return []  # Return empty list on datetime parsing error

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
