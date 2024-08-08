import logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)

logger = logging.getLogger(__name__)

import os
import utils.constants as constants

# get base directory to figure out where playbooks are located
if os.getenv("BASEDIR")==None:
    constants.basedir=os.getcwd()
else:
    constants.basedir=os.getenv("BASEDIR")
logger.info("Base directory is %s", constants.basedir)

# register lifecycle events
from services.servicesevents import *
from resources.wireguard.lifecycle import *

# Login with k8s client
@kopf.on.login()
def login_fn(**kwargs):
    return kopf.login_via_client(**kwargs)

