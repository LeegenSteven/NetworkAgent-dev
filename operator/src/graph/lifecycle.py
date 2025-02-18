import kopf
import logging
import utils.constants as constants
from utils.compute import *
from graph.lifecycle_tasks import *

logger = logging.getLogger(__name__)

# Catch create events
# TODO: see if you can be more specific in the Kopf resource
# specification without loosing genericity too much
@kopf.on.create(kopf.EVERYTHING, labels = {'graph': 'true'})
async def create_node(body, spec, meta, uid, namespace, name, logger, **kwargs):
  logger.debug("Create graph network node")
  success = True

  # We know that this node must be added to the graph
  # thanks to the filter on graph label
  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError(f"Graph node without UID ({kind}, {name}, {uid})")
  else:
    logger.debug("Graph node %s of kind %s detected", name, kind)
  
  success &= create_network_node(body, spec, namespace, name, kind, uid)

  # --- Build K8s resource connections (management connections)
  #
  # Note : if the owner reference doesn't exist it is the root node
  # that represents the network service deployed
  owner_ref = meta.get('ownerReferences')
  if owner_ref is not None:
    parent_uid = owner_ref[0]['uid']
    if parent_uid is None:
      raise kopf.PermanentError(f"Graph child node without parent UID ({kind}, {name}, {uid})")
    
    logger.debug("Creating resource connection from parent node %s to node %s", parent_uid, uid)
    success &= create_resource_connection(parent_uid, uid)

  # --- Build network connections (traffic connections)
  success &= await create_or_update_network_connections(body, spec, meta, uid, namespace, name)
  if not success:
    raise kopf.TemporaryError(f"Create node error ({kind}, {name}, {uid})", delay=15)
  else:
    logger.debug("Created node '%s' (success: %s) (%s, %s, %s)", name, success, kind, name, uid)


# Catch update events
@kopf.on.update(kopf.EVERYTHING, labels = {'graph': 'true'})
async def update_node(body, spec, meta, uid, namespace, name, logger, **kwargs):
  logger.debug("Update graph network node")
  success = True

  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError(f"Graph node without UID  ({kind}, {name}, {uid})")
  else:
    logger.debug("Graph node %s of kind %s detected", name, kind)

  # Update node attributes 
  logger.debug("Updating node attributes for uid %s (%s:%s)", uid, kind, name)
  success &= update_network_node(body, spec, namespace, name, kind, uid)

  # Update network connections
  success &= await create_or_update_network_connections(body, spec, meta, uid, namespace, name)

  if not success:
    raise kopf.TemporaryError(f"Update node error ({kind}, {name}, {uid})", delay=15)
  else:
    logger.debug("Updated node '%s' (success: %s) (%s, %s, %s)", name, success, kind, name, uid)

# Catch delete events
@kopf.on.delete(kopf.EVERYTHING, labels = {'graph': 'true'})
async def delete_node(body, spec, uid, name, logger, **kwargs):
  logger.debug("Delete graph network node")
  success = True

  # Check the node unique id
  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError(f"Graph node without UID ({kind}, {name}, {uid})")
  else:
    logger.debug("Graph node %s of kind %s detected", name, kind)

  # First delete all the network connections involving this node 
  # because of database consistency foreign key rule
  logger.debug("Deleting resource connections for uid %s (%s:%s)", uid, kind, name)
  success &= delete_node_resource_connections(uid, kind, name)
  logger.debug("Deleting network connections for uid %s (%s:%s)", uid, kind, name)
  success &= delete_node_network_connections(uid, kind, name)
  if success:
    logger.debug("Deleting network nodes for uid %s \n", uid)
    success &= delete_network_node(uid, kind, name)

  logger.debug("Deleted node '%s' (success: %s)", name, success)
  if not success:
    raise kopf.TemporaryError(f"Delete node error ({kind}, {name}, {uid})", delay=15)
  else:
    logger.debug("Deleted node '%s' (success: %s) (%s, %s, %s)", name, success, kind, name, uid)
