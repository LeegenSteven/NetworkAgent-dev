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
import google.auth
import os
from google.cloud import spanner

import streamlit as st



SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
@st.cache_resource
def spanner_connect():
  credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/agent/networkagent.json"))[0]
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

def fetch_log_entries():
  with database.snapshot() as snapshot:
    try:
      sql = "SELECT timestamp, severity, message FROM KgLogEntryNode ORDER BY timestamp DESC LIMIT 50"
      results = snapshot.execute_sql(sql)
    except Exception as e:
      logger.error("Log Entries SQL error: {}".format(e))
    return results.to_dict_list()

def format_rows(rows):
  output = ""
  for row in rows:
    output += "%s %-6s %s\n" % (f"{row['timestamp']}"[0:23], row['severity'], row['message'])  
  return output