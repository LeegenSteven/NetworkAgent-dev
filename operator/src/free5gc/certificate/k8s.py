import logging
import kubernetes
import googleapiclient.discovery
from tempfile import NamedTemporaryFile
import base64
import google.auth

logger = logging.getLogger(__name__)

def token(*scopes):
    credentials = google.auth.load_credentials_from_file("/operator/networkagent.json")[0]
    scopes = [f'https://www.googleapis.com/auth/{s}' for s in scopes]
    scoped = googleapiclient._auth.with_scopes(credentials, scopes)
    googleapiclient._auth.refresh_credentials(scoped)
    return scoped.token

def get_api_client(cluster):
    config = kubernetes.client.Configuration()
    config.host = f'https://{cluster.get("status").get("endpoint")}'

    config.api_key_prefix['authorization'] = 'Bearer'
    mytoken = token('cloud-platform')

    logger.debug(mytoken)
    config.api_key['authorization'] = mytoken

    with NamedTemporaryFile(delete=False) as cert:
        cert.write(base64.decodebytes(cluster.get("spec").get("masterAuth").get("clusterCaCertificate").encode()))
        config.ssl_ca_cert = cert.name

    client = kubernetes.client.ApiClient(configuration=config)

    return client

