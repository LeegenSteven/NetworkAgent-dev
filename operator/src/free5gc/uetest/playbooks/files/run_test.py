# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
        #logger.info(f"Successfully accessed: {url} - Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error accessing {url}: {e}")

if __name__ == "__main__":
    while True:
        for i in range(2000):
            send_request()
        time.sleep(random.uniform(3, 5))