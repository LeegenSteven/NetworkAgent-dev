import google.auth
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
import utils.constants as constants
import logging
import os

logger = logging.getLogger(__name__)

def login():
  credentials = google.auth.load_credentials_from_file(constants.SERVICE_FILE_LOCATION)[0]
  cluster_manager_client = ClusterManagerClient(credentials=credentials)

  name=f"projects/{constants.PROJECT}/locations/{constants.ZONE}/clusters/{constants.CLUSTER}"
  cluster = cluster_manager_client.get_cluster(name=name)

  SERVER = cluster.endpoint
  CERT = cluster.master_auth.cluster_ca_certificate

  NAME="networkautomation" # arbitrary
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

  yaml = YAML(typ='safe', pure=True)
  constants.KUBECONFIG = yaml.load(CONFIG)

  logger.info("writing KUBECONFIG file")
  with open(os.getenv("K8S_AUTH_KUBECONFIG"), 'w') as yaml_file:
    yaml.dump(constants.KUBECONFIG, yaml_file)

  logger.info("successfully logged into cluster - "+constants.CLUSTER)

  configuration = client.Configuration()
  loader = config.kube_config.KubeConfigLoader(constants.KUBECONFIG)
  loader.load_and_set(configuration)
  constants.api_client = client.ApiClient(configuration)
  constants.v1_api_instance = client.CoreV1Api(constants.api_client)
  constants.custom_api_instance = client.CustomObjectsApi(constants.api_client)
