import kopf
import logging
import ansible_runner
import os

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    logger.info("starting Network Agent")

