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

import os
import sys
import utils.constants as constants

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

import kopf

# get base directory to figure out where playbooks are located
if os.getenv("BASEDIR")==None:
    constants.basedir=os.getcwd()
else:
    constants.basedir=os.getenv("BASEDIR")
logger.info("Base directory is %s", constants.basedir)

# register lifecycle events
if os.getenv("VPN") is not None:
    logger.info("VPN Lifecycle")
    from vpn.pointtopoint.lifecycle import *
    from vpn.mesh.lifecycle import *
    from vpn.utils.status import *
    from vpn.wireguard.lifecycle import *

if os.getenv("FREE5GC") is not None:
    logger.info("FREE5GC Lifecycle")
    from free5gc.ueransim.lifecycle import *
    from free5gc.upf.lifecycle import *
    from free5gc.controlplane.lifecycle import *
    from free5gc.dnn.lifecycle import *
    from free5gc.uetest.lifecycle import *

if os.getenv("GITEA") is not None:
    logger.info("GITEA Lifecycle")
    from gitea.lifecycle import *

if os.getenv("GRAPH") is not None:
    from graph.lifecycle import *

if os.getenv("GOOGLE_REGION") is None or os.getenv("GOOGLE_ZONE") is None or os.getenv("GOOGLE_PROJECT") is None:
    logger.error("You must set GOOGLE_REGION/GOOGLE_ZONE/GOOGLE_PROJECT environment variables")
    sys.exit(0)

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.posting.level = logging.DEBUG
    settings.posting.enabled = True
    settings.watching.connect_timeout = 1 * 60
    settings.watching.server_timeout = 10 * 60
    settings.execution.max_workers = 5
    
# Login with k8s client
@kopf.on.login()
def login_fn(**kwargs):
    return kopf.login_via_client(**kwargs)
