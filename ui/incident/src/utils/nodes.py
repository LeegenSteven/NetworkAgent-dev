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
from agent_library.credentials.creds import get_credentials
from google.cloud import spanner

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
def spanner_connect():
  credentials,_ = get_credentials()
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
      # Get parent/child relationships where parent manages ComputeInstance child
      results = snapshot.execute_sql(

        """GRAPH networkGraph
          MATCH (parent)-[:Manages]->(child)
          WHERE child.kind="ComputeInstance"
          RETURN parent.id AS parent_id, 
                 parent.name AS parent_name, 
                 parent.kind AS parent_kind,
                 child.id AS child_id, 
                 child.name AS child_name, 
                 child.kind AS child_kind""")

    except Exception as e:
      logger.error("SQL error: {}".format(e))
      success = False

  elements = []
  if success:
    for row in results:
      elements.append({
        'parent': {
          'id': row[0], 
          'name': row[1] if row[1] else row[0],
          'kind': row[2] if row[2] else 'Unknown'
        },
        'child': {
          'id': row[3], 
          'name': row[4] if row[4] else row[3],
          'kind': row[5]
        }
      })
  
  return elements, success
