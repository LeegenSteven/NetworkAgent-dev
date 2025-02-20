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

import sys
import os
import base64
import json
import functions_framework
from cloudevents.http import CloudEvent

# Attach the Cloud Logging handler to the Python root logger 
# by calling the setup_logging method. By doing so Cloud Logging
# will properly report the logs severity for instance. If we do it
# directly (as above) all logs are classified with ERROR severity
# (see https://cloud.google.com/logging/docs/setup/python)
import google.cloud.logging
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

import logging
logger = logging.getLogger(__name__)

# After importing the Python standard logging library we end up with 2 log
# handlers at the root level causing duplicate log entries to appear
# in Cloud Logging, one that comes from the Cloud Logging Structured
# handler and the other from the standard Python StreamHandler
# Logger root handlers: [<StreamHandler <stderr> (NOTSET)>, <StructuredLogHandler <stderr> (NOTSET)>]
# Remove the standard Python logging handler to avoid duplicate (first handler in the list)
#del logging.getLogger().handlers[0]

# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner
SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

# This is to generate KG node embeddings
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
# Parameters for vertex AI embedding
TASK_TYPE = "QUESTION_ANSWERING"
EMBEDDING_MODEL_NAME="text-embedding-005"
EMBEDDING_MODEL = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

# See GCP LogEntry documentation
# https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry
Severity = {
  0   : "DEFAULT",	 # The log entry has no assigned severity level.
  100 : "DEBUG",	   # Debug or trace information.
  200 : "INFO",	     # Routine information, such as ongoing status or performance.
  300 : "NOTICE",	   # Normal but significant events, such as start up, shut down, or a configuration change.
  400 : "WARNING",	 # Warning events might cause problems.
  500 : "ERROR",	   # Error events are likely to cause problems.
  600 : "CRITICAL",  # Critical events cause more severe problems or outages.
  700 : "ALERT",	   # A person must take an action immediately.
  800 : "EMERGENCY", # One or more systems are unusable.
}

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

# Structure of the CloudEvent object received
# See https://cloud.google.com/eventarc/docs/cloudevents-json#pubsub
# See also https://cloud.google.com/functions/docs/tutorials/pubsub#functions_helloworld_pubsub_tutorial-python
"""
{
  'attributes': {
    'specversion': '1.0', 
    'id': '13722152750867042', 
    'source': '//pubsub.googleapis.com/projects/networkagent-434609/topics/nwoplogs-topic',
    'type': 'google.cloud.pubsub.topic.v1.messagePublished',
    'datacontenttype': 'application/json',
    'time': '2025-02-11T11:46:24.190Z'
  },
  'data': {
    'message': {
      'attributes': {
        'logging.googleapis.com/timestamp': '2025-02-11T11:46:19.219751861Z'
        },
      'data': 'eyJodHRwUmVxdWVzdCI6.....TE4NjFaIn0=', 
      'messageId': '13722152750867042', 
      'message_id': '13722152750867042', 
      'publishTime': '2025-02-11T11:46:24.19Z', 
      'publish_time': '2025-02-11T11:46:24.19Z'
    }, 
    'subscription': 'projects/networkagent-434609/subscriptions/eventarc-europe-west1-capture-log-375017-sub-476'
  }
}
"""

# ------------------------------------------
# Given a piece of text return the embedding (Array of Float64)
# ------------------------------------------
def get_embedding(text, task_type, model):
  try:
    text_embedding_input = TextEmbeddingInput(task_type=task_type, text=text)
    embeddings = model.get_embeddings([text_embedding_input])
    return embeddings[0].values
  except Exception as e:
    logger.error(f"Embedding error: {e}")
    return []

# ------------------------------------------
# Main Cloud Function entry point
# ------------------------------------------
@functions_framework.cloud_event
def capture_log(cloud_event: CloudEvent) -> None:
  def insert_log_entry(transaction):
    transaction.execute_update(
      "INSERT INTO KgLogEntryNode "
      "(id, severity, message, timestamp, content, embedding)"
      "VALUES (@id, @severity, @message, @timestamp, @content, @embedding)",
      params={
        "id": insert_id, 
        "severity": severity, 
        "message": message, 
        "timestamp": timestamp, 
        "content": content, 
        "embedding": embedding },
      param_types={
        "id": spanner.param_types.STRING, 
        "severity": spanner.param_types.STRING,
        "message": spanner.param_types.STRING, 
        "timestamp": spanner.param_types.TIMESTAMP,
        "content": spanner.param_types.STRING, 
        "embedding": spanner.param_types.Array(spanner.param_types.FLOAT64) },
    )

  logger.info(f">>> Received cloud event. Message: {cloud_event}")

  nwop_logging_data = cloud_event.data['message']['data']
  nwop_logging_json_string = base64.b64decode(nwop_logging_data).decode('utf-8')

  nwop_logging_json = json.loads(nwop_logging_json_string)

  insert_id = nwop_logging_json["insertId"]
  severity = nwop_logging_json["severity"] or "DEFAULT"
  message = nwop_logging_json["textPayload"]
  timestamp = nwop_logging_json["timestamp"]
  content = nwop_logging_json_string
  embedding = get_embedding(content, TASK_TYPE, EMBEDDING_MODEL)

  # In rare cases the vector generation fails and embedding 
  # comes back empty. Do not insert this log entry in Spanner
  # as it will result in error when searching by similarity
  if embedding:
    try:
      database.run_in_transaction(insert_log_entry)
    except Exception as e:
      logger.error(f"Log insert error: {e}")
