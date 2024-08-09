import kubernetes
import os
import logging
from pathlib import Path
logger = logging.getLogger(__name__)

def login():
    # check if kubeconfig path exists
    if os.path.exists(Path.home()/".kube"):
        logger.info("loading kube config")
        kubernetes.config.load_kube_config()
    else:
        logger.info("loading config from service account")
        kubernetes.config.load_incluster_config()