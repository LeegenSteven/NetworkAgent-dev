import logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)

logger = logging.getLogger(__name__)

import os
import sys
import utils.constants as constants

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
    from vpn.tests.lifecycle import *

if os.getenv("FREE5GC") is not None:
    logger.info("FREE5GC Lifecycle")
    from free5gc.build.lifecycle import *
    from free5gc.ueransim.lifecycle import *
    from free5gc.upf.lifecycle import *
    from free5gc.controlplane.lifecycle import *

if os.getenv("MONITOR") is not None:
    logger.info("MONITOR Lifecycle")
    from monitor.lifecycle import *

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
    settings.watching.connect_timeout = 1 * 60
    settings.watching.server_timeout = 10 * 60

# Login with k8s client
@kopf.on.login()
def login_fn(**kwargs):
    return kopf.login_via_client(**kwargs)
