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
from utils.creds import get_credentials
import json
from google.cloud import spanner

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

def get_nodes():
  results = []
  success = True

  with database.snapshot() as snapshot:
    try:
      results = snapshot.execute_sql(

        """GRAPH networkGraph
          MATCH (a)
          WHERE a.kind="ComputeInstance"
          RETURN a.id AS a_id, a.name""")

    except Exception as e:
      logger.error("SQL error: {}".format(e))
      success = False

  elements = []
  if success:
    for row in results:
      elements.append({'id': row[0], 'name': row[1]})
  
  return elements, success