#!/usr/bin/env python3
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
#
# This script collect cpu and network metrics of the host it is running on
# and send them to Spanner NetworkMetrics table.
# Adjust TIME_INTERVAL_SECONDS and MINUTES_OF_HISTORY to your liking
#

#------------ *** IMPORTANT REMARK *** --------------------------------
# This file is not executed in the operator itself
# It is actually copied by ansible on the VMs where
# monitoring is activated. We place it here to avoid duplicating it
# in each ansible playbook. 
# This file is the single source of truth

import psutil
import time
import os
import json

from google.cloud import spanner

#----------------------Send logging to GCP ----------------------------
# Attach the Cloud Logging handler to the Python root logger 
# by calling the setup_logging method. By doing so Cloud Logging
# will properly report the logs severity for instance. If we do it
# directly (as above) all logs are classified with ERROR severity
# (see https://cloud.google.com/logging/docs/setup/python)
import google.cloud.logging
logging_client = google.cloud.logging.Client()
import logging
logging_client.setup_logging(log_level=logging.INFO)

logger = logging.getLogger(__name__)

# After importing the Python standard logging library we end up with 2 log
# handlers at the root level causing duplicate log entries to appear
# in Cloud Logging, one that comes from the Cloud Logging Structured
# handler and the other from the standard Python StreamHandler
# Logger root handlers: [<StreamHandler <stderr> (NOTSET)>, <StructuredLogHandler <stderr> (NOTSET)>]
# Remove the standard Python logging handler to avoid duplicate (first handler in the list)
del logging.getLogger().handlers[0]
#-----------------------------------------------------------------------

# Adjust to you liking
POLLING_INTERVAL_IN_SECONDS = 5
HISTORY_IN_MINUTES = 10

HOSTNAME = os.uname().nodename
SQL_TEMPLATES = {
  'find_host_id': "SELECT id FROM NetworkNode WHERE name='{hostname}' and kind='ComputeInstance'",
  'exist_metrics': "SELECT id FROM NetworkMetrics WHERE id = '{id}'",
  'delete_metrics': "DELETE FROM NetworkMetrics WHERE id = '{id}'",
  'delete_old_metrics': "DELETE FROM NetworkMetrics WHERE id = '{id}' AND timestamp NOT IN ("
                    "SELECT timestamp FROM NetworkMetrics WHERE id = '{id}' ORDER BY timestamp DESC LIMIT {history})",
  'create_metrics': "INSERT NetworkMetrics (id, kind, name, timestamp, metrics)" 
                    " VALUES (@id, @kind, @name, @timestamp, @json_metrics)",
  'update_metrics': "UPDATE NetworkMetrics SET timestamp = {timestamp}, metrics = JSON '{metrics}' WHERE id = '{id}'",
}
CREDENTIAL_FILE = '/misc/applications/GCP/TME_projects/New_Network_Agent/NetworkAgent/networkagent.json'
SCRIPT_NAME = os.path.basename(__file__)
HISTORY = int(HISTORY_IN_MINUTES * (60 / POLLING_INTERVAL_IN_SECONDS)) # Number of metrics row we keep in history

# write a spanner SQL statement that delete all records in NetworkMetrics that are older than
# the most 180 most recent based on timestamp (hte bigger the timestamp the more recent the metrics is)

#In SQL how to only keep the top N records of a table sorted in descending order on a given column

# For reference : Spanner DDL to create the NetworkMetrics table
"""
  CREATE TABLE NetworkMetrics (
    id STRING(MAX) NOT NULL,
    kind STRING(MAX) NOT NULL,
    name STRING(MAX) NOT NULL,
    timestamp INT64 NOT NULL,
    metrics JSON
  ) PRIMARY KEY (id, timestamp);
"""

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()

def find_host_id(hostname):
  tmpl = SQL_TEMPLATES['find_host_id']
  sql = tmpl.format(hostname=hostname)
  logger.debug("SQL: {}".format(sql))

  try:
    id = None
    with database.snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    # results should return one record with the id or none. get the id value
    if (r := results.one_or_none()) is not None:
      id = r[0]
    success = (id is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.debug(f"{hostname} hostname exists (id: {id})")
  else:
    logger.debug(f"{hostname} hostname doesn't exist")
  return success, id

def exist_metrics(id):
  tmpl = SQL_TEMPLATES['exist_metrics']
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
    logger.debug(f"host id {id} metrics exists)")
  else:
    logger.debug(f"host id {id} metrics doesn't exist)")
  return success

def delete_metrics(id):
  def sql_delete_metrics(transaction):
    tmpl = SQL_TEMPLATES['delete_metrics']
    sql = tmpl.format(id=id)
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_metrics)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info(f"Metrics deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"Metrics deletion failed id: {id}")
  return success

def delete_old_metrics(id):
  def sql_delete_old_metrics(transaction):
    sql = SQL_TEMPLATES['delete_old_metrics'].format(id=id, history=HISTORY)
    logger.debug(f"SQL: {sql}")
    print(sql)
    return transaction.execute_update(sql)
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_delete_old_metrics)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.info(f"Old metrics deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"Old metrics deletion failed id: {id}")
  return success

def create_metrics(id, kind, name, timestamp, metrics):
  def sql_create_metrics(transaction):
    tmpl = SQL_TEMPLATES['create_metrics']
    sql = tmpl
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={
        "id": id, 
        "kind": kind,
        "name": name,
        "timestamp": timestamp,
        "json_metrics": metrics},
      param_types={
        "id": spanner.param_types.STRING,
        "kind": spanner.param_types.STRING,
        "name": spanner.param_types.STRING,
        "timestamp": spanner.param_types.INT64,
        "json_metrics": spanner.param_types.JSON})
  
  row_ct = 0
  success = True
  try:
    row_ct = database.run_in_transaction(sql_create_metrics)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")


def compute_metrics(metrics, last_net_io):
  metrics['timestamp'] = int(time.time())
  metrics['cpu']['cpu_percent'] = psutil.cpu_percent(None)

  current_net_io = psutil.net_io_counters(pernic=True)
  # Exclude loopback interface
  current_net_io.pop('lo', None)

  logger.debug("----------------------------------\n")
  logger.debug(f"*** current_net_io: {current_net_io}\n")
  logger.debug(f"*** last_net_io: {last_net_io}\n")

  iface_metrics = {}

  # If last_net_io is empty then this is the first iteration
  # and we cannot compute any deltas. So simply returns
  if last_net_io == {}:
    return (metrics, current_net_io)

  for iface, current_stats in current_net_io.items():
    logger.debug(f"iface item: {iface} => {current_stats}\n")

    if iface in last_net_io:
      last_stats = last_net_io[iface]
      # Calculate bytes sent/received during the interval
      bytes_sent = current_stats.bytes_sent
      bytes_recv = current_stats.bytes_recv
      bytes_sent_delta = bytes_sent - last_stats.bytes_sent
      bytes_recv_delta = bytes_recv - last_stats.bytes_recv
      bytes_sent_throughput = float(bytes_sent_delta) / POLLING_INTERVAL_IN_SECONDS
      bytes_recv_throughput = float(bytes_recv_delta) / POLLING_INTERVAL_IN_SECONDS
            
      iface_metrics[iface] = { 
        'byte_sent': bytes_sent, 
        'byte_sent_delta': bytes_sent_delta,
        'byte_recv': bytes_recv,
        'byte_recv_delta': bytes_recv_delta,
        'byte_sent_throughput': bytes_sent_throughput,
        'byte_recv_throughput': bytes_recv_throughput
        }
    else:
      # skip the first time a new interface appears as we cannot
      # compute deltas
      continue

  metrics['interfaces'] = iface_metrics

  return (metrics, current_net_io)

def save_metrics(id, metrics):
  #if exist_metrics(id):
  #delete_metrics(id)
  create_metrics(id, 'ComputeInstance', metrics['hostname'], metrics['timestamp'], json.dumps(metrics))


def poll_metrics(interval):
  cycles = 0
  last_net_io = {}
  current_net_io = {}
  metrics = {
    'hostname': HOSTNAME,
    'interval': interval,
    'cpu': {},
    'interfaces': {}
  }

  success, id = find_host_id(HOSTNAME)
  if not success:
    # This host id doesn't exist in DB - Do not save metrics
    logger.error(f"Host {hostname} has no uid in database. Metrics not saved. Exiting")
    exit(124)
  
  while True:
    time.sleep(interval)
    metrics, current_net_io = compute_metrics(metrics, last_net_io)

    # if first iteration do not write metrics to db
    if last_net_io == {}:
      last_net_io = current_net_io
      continue
    
    last_net_io = current_net_io
    logger.debug(f"{json.dumps(metrics, indent=2)}\n")
    save_metrics(id, metrics)
    cycles += 1

    # Every N cycles clean oldest metrics (don't do it
    # on each cycle to save processing time)
    if cycles == 20:
      delete_old_metrics(id)
      cycles = 0


#--- Helper for local testing (optional) ---
if __name__ == "__main__":
    
  # check if the GOOGLE_APPLICATION_CREDENTIALS env variable is set and file exist
  if 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ:
    logger.error("GOOGLE_APPLICATION_CREDENTIALS env variable not set")
    exit(1)
  if not os.path.exists(os.environ['GOOGLE_APPLICATION_CREDENTIALS']):
    logger.error(f"GOOGLE_APPLICATION_CREDENTIALS file {os.environ['GOOGLE_APPLICATION_CREDENTIALS']} doesn't exist")
    exit(1)

  # authenticate this application with GCP using the
  # application credentials in file networkagent.json
  #os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = CREDENTIAL_FILE

  # Fork this process to execute poll_metrics and restart it if it crashes

  while True:
    logger.info(f"Forking {SCRIPT_NAME}...")
    pid = os.fork()
    if pid == 0:
      # Child process
      logger.info(f"{SCRIPT_NAME} child process started on host {HOSTNAME}")
      poll_metrics(POLLING_INTERVAL_IN_SECONDS)
    else:
      try:
        pid, status = os.waitpid(pid, 0)
        logger.error(f"{SCRIPT_NAME} child process {pid} exited with status {status} on host {HOSTNAME}")
        if status == 124: exit(124)
        logger.info(f"Restarting child process {pid} on host {HOSTNAME}")
      except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Exiting.")
        exit(0)



