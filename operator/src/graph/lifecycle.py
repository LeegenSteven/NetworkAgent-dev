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
  logger.info("Create graph network node")
  success = False

  # We know that this node must be added to the graph
  # thanks to the filter on graph label
  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)
  
  success |= create_network_node(body, spec, namespace, name, kind, uid)

  # --- Build K8s resource connections (management connections)
  #
  # if parent name doesn't exist it is the root node
  # that represents the network service deployed
  owner_ref = meta.get('ownerReferences')
  if owner_ref is not None:
    parent_uid = owner_ref[0]['uid']
    if parent_uid is None:
      raise kopf.PermanentError("Graph child node without parent UID")
    
    logger.info("Creating resource connection from parent node %s to node %s", parent_uid, uid)
    success |= create_resource_connection(parent_uid, uid)

  # --- Build network connections (traffic connections)
  #
  # For all resources except ComputeInstance look for 
  # networkRef and subNetworkRef attributes in spec
  # For ComputeInstances look for the same fields in the list of NICs
  # under spec/networkInterface 
  if body['kind'] == 'ComputeInstance':
    specs = spec['networkInterface'] or []
  else:
    specs = [spec]

  for s in specs:
    logger.info("Looking for (sub)network ref in %s / %s", body.get('kind'), meta.get('name'))
    xnet = await find_network_reference(namespace, s)
    if xnet is not None:
      xnet_uid = xnet.get('metadata').get('uid')
      success |= create_network_connection(uid,xnet_uid)

  logger.info("Created node '%s' (success: %s)", name, success)
  if not success:
      raise kopf.TemporaryError("Create node error", delay=15)

# Catch update events
@kopf.on.update(kopf.EVERYTHING, labels = {'graph': 'true'})
async def update_node(body, spec, uid, name, logger, **kwargs):
  logger.info("Update graph network node")
  success = False

  kind = body.get('kind')
  if uid is None:
    raise kopf.PermanentError("Graph node without UID")
  else:
    logger.info("Graph node %s of kind %s detected", name, kind)

  logger.info("Updating node for uid %s (%s:%s)", uid, kind, name)
  success |= update_network_node(body, spec, name, kind, uid)

  logger.info("Updated node '%s' (success: %s)", name, success)
  if not success:
    raise kopf.TemporaryError("Update node error", delay=15)

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
  logger.info("Deleting resource connections for uid %s (%s:%s)", uid, kind, name)
  success |= delete_resource_connection(uid)
  logger.info("Deleting network connections for uid %s (%s:%s)", uid, kind, name)
  success |= delete_network_connection(uid)
  if success:
    logger.info("Deleting network nodes for uid %s \n", uid)
    success |= delete_network_node(uid)

  logger.info("Deleted node '%s' (success: %s)", name, success)
  if not success:
    raise kopf.TemporaryError("Delete node error", delay=15)
