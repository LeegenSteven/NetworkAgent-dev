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
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
from pathlib import Path
import os
import utils.constants as constants

logger = logging.getLogger(__name__)

def external_service_account(clusterName):
    credentials = google.auth.load_credentials_from_file(constants.basedir+'/networkagent.json')[0]
    cluster_manager_client = ClusterManagerClient(credentials=credentials)

    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    GOOGLE_REGION = os.getenv("GOOGLE_REGION")

    name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_REGION}/clusters/{clusterName}"
    cluster = cluster_manager_client.get_cluster(name=name)

    SERVER = cluster.endpoint
    CERT = cluster.master_auth.cluster_ca_certificate

    NAME=f"gke_{GOOGLE_PROJECT}_{GOOGLE_REGION}_{clusterName}" # arbitrary
    CONFIG=f"""
    apiVersion: v1
    kind: Config
    clusters:
    - name: {NAME}
      cluster:
        certificate-authority-data: {CERT}
        server: https://{SERVER}
    contexts:
    - name: {NAME}
      context:
        cluster: {NAME}
        user: {NAME}
    current-context: {NAME}
    users:
    - name: {NAME}
      user:
        auth-provider:
          name: gcp
          config:
            scopes: https://www.googleapis.com/auth/cloud-platform
    """

    logger.debug(CONFIG)
    yaml = YAML(typ='safe', pure=True)
    KUBECONFIG = yaml.load(CONFIG)

    configuration = client.Configuration()
    loader = config.kube_config.KubeConfigLoader(KUBECONFIG)
    loader.load_and_set(configuration)
    apiclient = client.ApiClient(configuration=configuration)

    return apiclient

def get_credentials():
    return google.auth.load_credentials_from_file(constants.basedir+'/networkagent.json')[0]

def get_client():
    # check if kubeconfig path exists
    if os.path.exists(Path.home()/".kube"):
        logger.debug("loading kube config")
        config.load_kube_config()
        return client.ApiClient()
    else:
        logger.debug("loading config from service account")
        try:
          logger.debug("trying k8s service account")
          config.load_incluster_config()
          return client.ApiClient()
        except Exception as e:
          logger.debug("falling back to GCP service account")
          return external_service_account()    