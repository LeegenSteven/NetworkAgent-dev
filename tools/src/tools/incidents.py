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
import utils.git_helpers as git
import utils.globals as globals
import json
from typing import Annotated, Dict, List
from datetime import datetime
from google.cloud import spanner
from utils.k8s import get_credentials

logger = logging.getLogger(__name__)

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

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

# Connect to Spanner database
def spanner_connect():
  credentials = get_credentials()
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

######################################################################
# Resource to provide the incident operating procedure doc
######################################################################
@globals.networkagent_mcp.tool()
def getIncidentOperatingProcedure()-> str:
    """
    Fetch the current incident method of operations file from git

    Returns:
        incident operations document in markdown format
    """
    logger.info("Getting incident operations guide from git")

    filename = "operating_procedure.md"
    result = git.get_git_file(git.INCIDENT_REPO, filename)
    if result is not None:
        return result
    else:
        logger.error(f"{filename} could not be found")
        return None


######################################################################
# Update Incident 
######################################################################
@globals.networkagent_mcp.tool()
def updateIncident(
    id: Annotated[str,"the id of the incident"],
    strategy: Annotated[Dict,"the strategy to investigate"],
    root_cause: Annotated[str,"the root cause identified"],
    resolution: Annotated[str,"the resolution to correct"]
    ):
    logger.info("updating incident task %s", id)

    # Validate required parameter
    if not id:
        logger.error("Incident ID is required")
        return None

    # Build the SET clause dynamically based on non-None parameters
    set_clauses = []
    params = {}
    param_types = {}
    
    if strategy is not None and strategy:
        strategy_data = {"strategy": strategy}
        strategy_json = json.dumps(strategy_data, ensure_ascii=True)
        strategy_json_spanner = body_sql_json_dump(strategy_json)
        set_clauses.append(f"strategy = JSON '{strategy_json_spanner}'")
    
    if root_cause is not None and root_cause:
        set_clauses.append("root_cause = @root_cause")
        params['root_cause'] = root_cause
        param_types['root_cause'] = spanner.param_types.STRING
    
    if resolution is not None and resolution:
        set_clauses.append("resolution = @resolution")
        params['resolution'] = resolution
        param_types['resolution'] = spanner.param_types.STRING
        # resolved_timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        # set_clauses.append("resolvedTimestamp = @resolved_timestamp")
        # params['resolved_timestamp'] = resolved_timestamp_ms
        # param_types['resolved_timestamp'] = spanner.param_types.INT64

    # If no fields to update, return early
    if not set_clauses:
        logger.warning("No fields provided to update for incident %s", id)
        return id

    # Build the UPDATE statement
    set_clause = ", ".join(set_clauses)
    upsert = f"UPDATE Incident SET {set_clause} WHERE id = @incident_id"
    params['incident_id'] = id
    param_types['incident_id'] = spanner.param_types.STRING
    
    logger.info(f"SQL: {upsert}")
    logger.info(f"Params: {params}")

    try:
        def update_incident(transaction):
            transaction.execute_update(upsert, params=params, param_types=param_types)

        database.run_in_transaction(update_incident)

        logger.info(f"Updated incident: {id}")
        return id

    except Exception as e:
        logger.error(f"Error updating incident: {e}")
        return None


######################################################################
# Add post mortem
######################################################################
@globals.networkagent_mcp.tool()
def addPostMortem(
    postmortem: Annotated[str,"the post mortem doc to store"]
    ):
    logger.info("adding new post mortem", id)


######################################################################
# Ping
######################################################################
