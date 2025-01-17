import logging
import google.auth
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client,config
from ruamel.yaml import YAML
from pathlib import Path
import os

logger = logging.getLogger(__name__)

def external_service_account():
    credentials = get_credentials()
    cluster_manager_client = ClusterManagerClient(credentials=credentials)

    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    GOOGLE_REGION = os.getenv("GOOGLE_REGION")
    GOOGLE_ZONE = os.getenv("GOOGLE_ZONE")

    name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_ZONE}/clusters/networkautomation"
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

def get_credentials():
    return google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/tools/networkagent.json"))[0]

def get_client():
    # check if kubeconfig path exists
    if os.path.exists(Path.home()/".kube"):
        logger.info("loading kube config")
        config.load_kube_config()
        return client.ApiClient()
    else:
        logger.info("loading config from service account")
        try:
          logger.info("trying k8s service account")
          config.load_incluster_config()
          return client.ApiClient()
        except Exception as e:
          logger.info("falling back to GCP service account")
          return external_service_account()    