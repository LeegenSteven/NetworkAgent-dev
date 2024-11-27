import logging
import kopf
from utils.k8s import getClusterDetails
from free5gc.utils.k8s import get_api_client
from free5gc.certificate.lifecycle_tasks import *

logger = logging.getLogger(__name__)

##########################################
# Create a git certificate in a cluster
##########################################
@kopf.on.create('google.dev', 'v1', 'gitcertificates')
async def creategitcertificate(spec, **_):

  # get the name and namespace
  name = spec.get("cluster").get('name')
  namespace = spec.get("cluster").get('namespace')
  if name is None or namespace is None:
    raise kopf.PermanentError("Name and Namespace must be set")

  logger.debug("Creating GIT Certificate for %s %s", name, namespace)
  
  cluster = await getClusterDetails(namespace, name)
  if cluster is None:
    raise kopf.TemporaryError("No Cluster Resource", 30)

  status = cluster.get("status").get("conditions")[0]
  logger.debug(status)
  if status is None or status.get("reason") != "UpToDate":
    raise kopf.TemporaryError("Waiting for cluster to be ready", 20)

  # create namespace and git secret in the new cluster
  api_client= get_api_client(cluster.get("status").get("endpoint"), cluster.get("spec").get("masterAuth").get("clusterCaCertificate"))
  await create_namespace(api_client, "config-management-system")
  await addSecret(api_client)
