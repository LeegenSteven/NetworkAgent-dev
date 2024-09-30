import kopf
import logging
import utils.constants as constants
from graph.lifecycle_tasks import *

logger = logging.getLogger(__name__)

# Catch create events
# TODO: see if you can be more specific in the Kopf resource
# specification without loosing genericity too much
@kopf.on.create(kopf.EVERYTHING, labels = {'graph': 'true'})
async def create_node(body, spec, meta, uid, name, logger, **kwargs):
  logger.info("Create graph network node")
  success = False

  # We know that this node must be added to the graph
  # thanks to the filter on graph label
  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)
  
  success |= create_network_node(body, name, kind, uid)

  # if parent name doesn't exist it is the root node
  # that represents the network service deployed
  owner_ref = meta.get('ownerReferences')
  if owner_ref is not None:
    parent_uid = owner_ref[0]['uid']
    if parent_uid is None:
      raise kopf.PermanentError("Graph child node without parent UID")
    
    logger.info("Linking node %s to parent node %s", uid, parent_uid)
    success |= create_network_connection(parent_uid, uid)

    logger.info("Create node '%s' (success: %s)", name, success)
    if not success:
      raise kopf.TemporaryError("Create node error", delay=15)

# Catch delete events
@kopf.on.delete(kopf.EVERYTHING, labels = {'graph': 'true'})
async def delete_node(body, spec, uid, name, logger, **kwargs):
  logger.info("Delete graph network node")
  success = False

  # Check the node unique id
  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)

  # delete network connections first because of database
  # consistency foreign key rule
  logger.info("Deleting network connections for uid %s (%s:%s)", uid, kind, name)
  success |= delete_network_connection(uid)
  if success:
    logger.info("Deleting network nodes for uid %s \n", uid)
    success |= delete_network_node(uid)

  logger.info("Delete node '%s' (success: %s)", name, success)
  if not success:
    raise kopf.TemporaryError("Delete node error", delay=15)
