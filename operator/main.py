import logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)

logger = logging.getLogger(__name__)

# get base directory
import os
import utils.constants as constants
constants.basedir=os.getcwd()
if constants.basedir == '/':
    constants.basedir = ''
logger.info("Base directory is %s", constants.basedir)

# register events
from services.servicesevents import *
from resources.wireguard.wireguardevents import *
import utils.login as k8slogin

@kopf.on.login()
def login_fn(**kwargs):
    if os.getenv("K8S_AUTH_KUBECONFIG") is not None:
        k8slogin.login()
    return kopf.login_via_client(**kwargs)

