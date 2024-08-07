import logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)

logger = logging.getLogger(__name__)

# get base directory to figure out where playbooks are located
import os
import utils.constants as constants
constants.basedir=os.getcwd()
if constants.basedir == '/':
    constants.basedir = ''
logger.info("Base directory is %s", constants.basedir)

# register lifecycle events
from services.servicesevents import *
from resources.wireguard.wireguardevents import *


@kopf.on.login()
def login_fn(**kwargs):
    return kopf.login_via_client(**kwargs)

