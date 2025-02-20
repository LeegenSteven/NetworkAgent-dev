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
import kubernetes
import googleapiclient.discovery
from tempfile import NamedTemporaryFile
import base64
import google.auth
from google.cloud.container_v1 import ClusterManagerClient
import os


logger = logging.getLogger(__name__)

##########################################################
# get GCP auth token
##########################################################
def token(*scopes):
    credentials = google.auth.load_credentials_from_file("/operator/networkagent.json")[0]
    scopes = [f'https://www.googleapis.com/auth/{s}' for s in scopes]
    scoped = googleapiclient._auth.with_scopes(credentials, scopes)
    googleapiclient._auth.refresh_credentials(scoped)
    return scoped.token

##########################################################
# Given a public cluster endopoint and certificate return 
# kubernetes api client
##########################################################
def get_api_client(endpoint, certificate):
    config = kubernetes.client.Configuration()
    config.host = f'https://{endpoint}'

    config.api_key_prefix['authorization'] = 'Bearer'
    mytoken = token('cloud-platform')

    logger.debug(mytoken)
    config.api_key['authorization'] = mytoken

    with NamedTemporaryFile(delete=False) as cert:
        cert.write(base64.decodebytes(certificate.encode()))
        config.ssl_ca_cert = cert.name

    client = kubernetes.client.ApiClient(configuration=config)

    return client

##########################################################
# get the external ip address of the named cluster
##########################################################
async def getClusterIP(name):
    logger.debug("get cluster external ip for %s", name)
    credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))[0]
    cluster_manager_client = ClusterManagerClient(credentials=credentials)
    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    GOOGLE_REGION = os.getenv("GOOGLE_REGION")
    clustername=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_REGION}/clusters/{name}"
    cluster = cluster_manager_client.get_cluster(name=clustername)
    return cluster.endpoint

##########################################################
# Get all cluster details
##########################################################
async def getClusterDetails(clustername):
  logger.debug("get cluster details for %s", clustername)
  credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))[0]
  cluster_manager_client = ClusterManagerClient(credentials=credentials)
  GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
  GOOGLE_ZONE = os.getenv("GOOGLE_ZONE")
  name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_ZONE}/clusters/{clustername}"
  cluster = cluster_manager_client.get_cluster(name=name)
  return cluster
