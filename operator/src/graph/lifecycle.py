import kopf
import logging
import utils.constants as constants
from graph.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev', 'v1', kopf.EVERYTHING, labels = {'graph': 'true'})
async def create_node(body, spec, metadata, name, logger, **kwargs):
  logger.info("Create graph network node")
  success = False

  # We know that this node must be added to the graph
  # thanks to the filter on graph label
  name = spec.get('name')
  kind = spec.get('kind')
  uid = metadata.get('uid')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)
  
  success |= create_network_node(body, name, kind, uid)

  # if parent name doesn't exist it is the root node
  # that represents the network service deployed
  owner_ref = metadata.get('owner_references')
  if owner_ref is None:
    parent_uid = owner_ref.get('UID')
    if parent_uid is None:
      raise kopf.PermanentError("Graph node without parent UID")
    
    logger.info("Linking node %s to parent node %s\n", uid[-9:-1], parent_uid[-9:-1])
    success |= create_network_connection(uid, parent_uid)

    logger.info("Create node success = %s", success)
    if not success:
      logger.info(json.dumps(body, indent=2))
      raise kopf.TemporaryError("Create node error", delay=15)


@kopf.on.delete('google.dev', 'v1', kopf.EVERYTHING, labels = {'graph': 'true'})
async def deletetest(body, spec, metadata, logger, **kwargs):
  logger.info("Delete graph network node")
  success = False

  # Get the node unique id
  name = spec.get('name')
  kind = spec.get('kind')
  uid = metadata.get('uid')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)

  # delete network connections first because of database
  # consistency foreign key rule
  logger.info("Deleting network connections for uid %s \n", uid[-9:-1])
  success |= delete_network_connection(uid)
  if success:
    logger.info("Deleting network nodes for uid %s \n", uid[-9:-1])
    success |= delete_network_node(uid)

  logger.info("Create node success = %s", success)
  if not success:
    logger.info(json.dumps(body, indent=2))
    raise kopf.TemporaryError("Delete node error", delay=15)

