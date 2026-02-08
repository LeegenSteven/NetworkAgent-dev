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

import kopf
import logging
import utils.constants as constants
from utils.compute import *
from graph.lifecycle_tasks import *

logger = logging.getLogger(__name__)

# --- VyOS Handlers ---

# GRAPH networkGraph
# MATCH router_path = (r1:PhysicalRouter)-[:HasInterface]->(:PhysicalInterface)
#       -[:ConnectsTo]->(:PhysicalLink)
#       -[:LinkedTo]->(:PhysicalInterface)
#       <-[:HasInterface]-(r2:PhysicalRouter)
# WHERE r1.id < r2.id
# RETURN SAFE_TO_JSON(router_path) AS router_path
# LIMIT 40;


@kopf.on.event('vyosinfrastructures.google.dev', id='vyosinfrastructure-graph')
async def obj_vyosinfrastructure(body, spec, name, uid, logger, event, **kwargs):
    if event['type'] == 'DELETED':
       await delete_vyos_infrastructure(uid, spec, logger)
    else:
       await sync_vyos_infrastructure(body, spec, name, uid, logger)

@kopf.on.create('vyosrouters.google.dev', id='vyosrouter-create-graph')
async def obj_vyosrouter_create(body, spec, name, uid, logger, **kwargs):
    """Sync new VyOSRouter to Spanner"""
    logger.info(f"VyOSRouter {name} created - syncing to Spanner")
    await sync_physical_router(body, spec, name, uid, logger)

@kopf.on.update('vyosrouters.google.dev', field='spec', id='vyosrouter-spec-graph')
async def obj_vyosrouter_spec_update(body, spec, name, uid, logger, **kwargs):
    """Sync VyOSRouter spec changes to Spanner"""
    logger.info(f"VyOSRouter {name} spec updated - syncing to Spanner")
    await sync_physical_router(body, spec, name, uid, logger)

@kopf.on.delete('vyosrouters.google.dev', id='vyosrouter-delete-graph')
async def obj_vyosrouter_delete(name, uid, logger, **kwargs):
    """Delete VyOSRouter from Spanner"""
    logger.info(f"VyOSRouter {name} deleted - removing from Spanner")
    await delete_physical_router(uid, name=name)

@kopf.on.event('vyosl3vpns.google.dev', id='vyosl3vpn-graph')
async def obj_vyosl3vpn(body, spec, name, uid, logger, event, **kwargs):
    if event['type'] == 'DELETED':
       await delete_l3vpn_service(uid)
    else:
       await sync_l3vpn_service(body, spec, name, uid, logger)
