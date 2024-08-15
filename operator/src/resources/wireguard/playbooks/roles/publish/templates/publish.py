from google.cloud import pubsub_v1
import requests
import google.auth
import logging
import socket
import json
import datetime
import time

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)


publisher = None
running = True

def poll():

    while(running):

        # get metrics from node exporter every 10 seconds
        r = requests.get('http://127.0.0.1:9100/metrics')

        rawlines = r.text.splitlines()
        metrics={"servicename": "{{servicename}}", "device": "wg0", "edgename": socket.gethostname(), "time": str(datetime.datetime.now())}
        for line in rawlines:
            if not line.startswith('#'):
                if "wg0" in line and ('transmit' in line or 'receive' in line):
                    if 'bytes' in line or 'drop' in line or 'packets' in line:
                        strings=line.split()
                        key = strings[0].split('{')[0]
                        value = strings[1]
                        metrics[key]=int(value)

        json_metrics=json.dumps(metrics)
        logger.info(json_metrics)
        data = json_metrics.encode('utf-8')

        future = publisher.publish(topic_name, data)
        future.result()

        time.sleep(10)

if __name__ == "__main__":
    credentials = google.auth.load_credentials_from_file("/opt/networkagent.json")[0]
    logging.info(credentials)

    publisher = pubsub_v1.PublisherClient(credentials=credentials)
    topic_name = 'projects/{{ GOOGLE_PROJECT }}/topics/serviceperformance'
    try:
        publisher.create_topic(name=topic_name)
        logger.info("created topic")
    except google.api_core.exceptions.AlreadyExists:
        logger.info("topic already exists")

    poll()    