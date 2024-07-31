import logging
log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)

logger = logging.getLogger(__name__)

from wireguard.wireguardevents import *

if __name__ == '__main__':
    logger.info("starting Network Agent")

