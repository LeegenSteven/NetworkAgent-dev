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
import os
import json
import base64
from flask import Flask, request, jsonify
from datetime import datetime

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def decode_pubsub_message(message_data):
    logger.info("decode pubsub message")
    """Decode the Pub/Sub message data from base64."""
    try:
        if message_data:
            decoded_data = base64.b64decode(message_data).decode('utf-8')
            return decoded_data
    except Exception as e:
        logger.error(f"Error decoding message data: {e}")
        return None

def process_fault_event(event_data):
    logger.info("process fault event")
    """Process the fault event and extract relevant information."""
    try:
        # Parse the log entry if it's JSON
        if event_data.startswith('{'):
            log_entry = json.loads(event_data)
            
            # Extract relevant fields from the log entry
            timestamp = log_entry.get('timestamp', 'Unknown')
            severity = log_entry.get('severity', 'Unknown')
            log_name = log_entry.get('logName', 'Unknown')
            resource = log_entry.get('resource', {})
            labels = log_entry.get('labels', {})
            text_payload = log_entry.get('textPayload', '')
            json_payload = log_entry.get('jsonPayload', {})
            
            logger.info(f"=== FAULT EVENT DETECTED ===")
            logger.info(f"Timestamp: {timestamp}")
            logger.info(f"Severity: {severity}")
            logger.info(f"Log Name: {log_name}")
            logger.info(f"Resource: {resource}")
            logger.info(f"Labels: {labels}")
            logger.info(f"Text Payload: {text_payload}")
            logger.info(f"JSON Payload: {json_payload}")
            logger.info(f"=== END FAULT EVENT ===")
            
            # Check for different types of fault events
            python_logger = labels.get('python_logger', '')
            if python_logger == 'UERANSIMHEALTH':
                logger.warning(f"UERANSIM HEALTH ISSUE DETECTED: {text_payload}")
                process_name = json_payload.get('process_name', 'N/A')
                hostname = json_payload.get('hostname', 'N/A')
                logger.error(f"Details: Process '{process_name}' not running on host '{hostname}'.")

            elif python_logger == 'CRITICALSERVICEERROR':
                logger.warning(f"CRITICAL SERVICE ERROR DETECTED: {text_payload}")
                url = json_payload.get('url', 'N/A')
                user = json_payload.get('userid', 'N/A')
                node = json_payload.get('node', 'N/A')
                error = json_payload.get('error', 'N/A')
                logger.error(f"Details: Error '{error}' for user '{user}' when accessing '{url}' from node '{node}'.")
                
        else:
            # Handle plain text log entries
            logger.info(f"=== FAULT EVENT (TEXT) ===")
            logger.info(f"Event Data: {event_data}")
            logger.info(f"=== END FAULT EVENT ===")
            
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON log entry: {e}")
        logger.info(f"Raw event data: {event_data}")
    except Exception as e:
        logger.error(f"Error processing fault event: {e}")

@app.route('/', methods=['POST'])
def handle_eventarc():
    logger.info("EVENT Received")
    """Handle incoming Eventarc events from Pub/Sub."""
    try:
        # Get the CloudEvent headers
        ce_type = request.headers.get('ce-type', 'Unknown')
        ce_source = request.headers.get('ce-source', 'Unknown')
        ce_subject = request.headers.get('ce-subject', 'Unknown')
        ce_time = request.headers.get('ce-time', 'Unknown')
        
        logger.info(f"Received Eventarc event:")
        logger.info(f"  Type: {ce_type}")
        logger.info(f"  Source: {ce_source}")
        logger.info(f"  Subject: {ce_subject}")
        logger.info(f"  Time: {ce_time}")
        
        # Get the request data
        event_data = request.get_json()
        
        if event_data:
            logger.info(f"Event data: {json.dumps(event_data, indent=2)}")
            
            # Extract the Pub/Sub message
            message = event_data.get('message', {})
            if message:
                # Decode the message data
                message_data = message.get('data', '')
                decoded_message = decode_pubsub_message(message_data)
                
                if decoded_message:
                    logger.info(f"Decoded message: {decoded_message}")
                    process_fault_event(decoded_message)
                
                # Log message attributes
                attributes = message.get('attributes', {})
                if attributes:
                    logger.info(f"Message attributes: {json.dumps(attributes, indent=2)}")
                
                # Log message ID and publish time
                message_id = message.get('messageId', 'Unknown')
                publish_time = message.get('publishTime', 'Unknown')
                logger.info(f"Message ID: {message_id}")
                logger.info(f"Publish Time: {publish_time}")
        
        return jsonify({'status': 'success', 'message': 'Event processed'}), 200
        
    except Exception as e:
        logger.error(f"Error handling Eventarc event: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'service': 'fault-service'}), 200

@app.route('/', methods=['GET'])
def root():
    """Root endpoint for basic info."""
    return jsonify({
        'service': 'Network Fault Service',
        'status': 'running',
        'description': 'Processes fault events from UERANSIM health monitoring'
    }), 200

if __name__ == "__main__":
    logger.info("Starting Network Fault Service...")
    
    # Get port from environment variable (Cloud Run sets this)
    port = int(os.environ.get('PORT', 8080))
    
    logger.info(f"Service will listen on port {port}")
    logger.info("Ready to receive fault events from Eventarc/Pub/Sub")
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
