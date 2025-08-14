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

import time
import os
import psutil

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

HOSTNAME = os.uname().nodename
CREDENTIAL_FILE = '/opt/networkagent.json'
SCRIPT_NAME = os.path.basename(__file__)

# --- Configuration ---
POLLING_INTERVAL_IN_SECONDS = 5
PROCESS_NAME = "./nr-gnb"

def is_process_running():
    """Check if the specified process is running and log error if not."""
    process_found = False
    
    # Check all running processes
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if the process name or command line contains our target process
            if proc.info['cmdline']:
                cmdline = ' '.join(proc.info['cmdline'])
                if PROCESS_NAME in cmdline or PROCESS_NAME in proc.info['name']:
                    process_found = True
                    logger.info(f"Process {PROCESS_NAME} is running (PID: {proc.info['pid']})")
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Process might have terminated or we don't have access
            continue
    
    if not process_found:
        error_msg = f"CRITICAL: Process {PROCESS_NAME} is not running on host {HOSTNAME}"
        logger.error(error_msg)
        print(f"ERROR: {error_msg}")  # Also print to stdout for debugging

def main():
    """Main loop to monitor the process."""
    logger.info(f"Starting ueransim  on host {HOSTNAME}")
    while True:
        is_process_running()
        time.sleep(POLLING_INTERVAL_IN_SECONDS)

if __name__ == "__main__":
    main()
