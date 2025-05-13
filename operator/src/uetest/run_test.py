#!/usr/bin/env python3
import requests
import time
import random
import os
import logging

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)

url = os.getenv("TEST_URL")

def send_request():
    try:
        response = requests.get(url)
        response.raise_for_status() 
        logger.info(f"Successfully accessed: {url} - Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error accessing {url}: {e}")

if __name__ == "__main__":
    while True:
        send_request()
        time.sleep(random.uniform(1, 5))