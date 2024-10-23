import logging
import google.auth
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
import os

logger = logging.getLogger(__name__)

def get_api_client_for_cluster(clustername):
  credentials = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))[0]
  cluster_manager_client = ClusterManagerClient(credentials=credentials)

  GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
  GOOGLE_REGION = os.getenv("GOOGLE_REGION")
  GOOGLE_ZONE = os.getenv("GOOGLE_ZONE")

  name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_ZONE}/clusters/{clustername}"
  cluster = cluster_manager_client.get_cluster(name=name)

  SERVER = cluster.endpoint
  CERT = cluster.master_auth.cluster_ca_certificate

  NAME=f"gke_{GOOGLE_PROJECT}_{GOOGLE_ZONE}_networkautomation" # arbitrary
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
      namespace: automation
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

