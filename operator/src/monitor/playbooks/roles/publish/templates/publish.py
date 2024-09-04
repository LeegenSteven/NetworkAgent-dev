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

        receive_txt = requests.get('http://127.0.0.1:9090/api/v1/query',params={'query': 'increase(node_network_receive_bytes_total[20s])'}).text
        json_data = json.loads(receive_txt)

        # transmit_txt = requests.get('http://127.0.0.1:9090/api/v1/query',params={'query': 'increase(node_network_transmit_bytes_total[20s])'}).text
        # json_data = json.loads(transmit_txt)

        if json_data['status'] == 'success':
            if len(json_data['data']['result']) !=0:
                json_metrics=json.dumps(json_data['data']['result'])
                # logger.info(json_metrics)
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