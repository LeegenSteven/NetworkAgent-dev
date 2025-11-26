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

from aiohttp import web
import aiohttp_cors
import logging
import os
from utils.nodes import get_nodes
import utils.constants as constants
from ansible.runner import run_incident

log_format = "%(asctime)s::%(levelname)s::%(name)s::"             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# get the base directory for this file
constants.basedir = os.path.dirname(os.path.realpath(__file__))
logger.info("Base directory is %s", constants.basedir)

# web app
app = web.Application()

# Setup CORS for aiohttp routes
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

#########################################################################
# return location of react ui
#########################################################################
async def serve_static_file(request):
    return web.FileResponse(os.path.join(constants.basedir, '..', 'frontend', 'build', 'index.html'))

#########################################################################
# login
#########################################################################
async def login(request):
    logger.info("login")

    data = await request.json()
    username = data.get('username')
    password = data.get('password')

    # Retrieve credentials from environment variables
    app_user = os.environ.get('WEBAPPS_LOGIN', 'admin')
    app_password = os.environ.get('WEBAPPS_PWD', 'password')

    logger.info(f"Authenticating with user: {app_user} and password: {app_password}")

    if username == app_user and password == app_password:
        return web.json_response({'success': True, 'token': 'dummy-token'})
    else:
        return web.json_response({'success': False, 'message': 'Invalid credentials'}, status=401)

#########################################################################
# return computeinstance nodes and their parents
#########################################################################
async def fetch_nodes(request):
    logger.info("get nodes")

    nodes,success=get_nodes()
    if success:
        return web.json_response(nodes)
    else:
        return web.json_response([])

#########################################################################
# kill a process on a node
#########################################################################
async def kill_process(request):
    logger.info("kill process on node")
    
    try:
        data = await request.json()
        node = data.get('node')
        incident_type = data.get('incident_type')
        
        if not node or not incident_type:
            return web.json_response({'error': 'Missing node or incident_type'}, status=400)
        
        # Extract node information
        parent_node = node.get('parent', {})
        child_node = node.get('child', {})
        
        logger.info(f"Processing {incident_type} incident for node: {child_node.get('name')} (kind: {child_node.get('kind')})")
        logger.info(f"Parent node: {parent_node.get('name')} (kind: {parent_node.get('kind')})")
        
        # Run the incident with node and incident type information
        await run_incident(
            parent_node=parent_node,
            parent_kind=parent_node.get('kind'),
            child_node=child_node,
            child_kind=child_node.get('kind'),
            incident_type=incident_type
        )
        
        return web.json_response({'success': True, 'message': f'{incident_type} incident initiated on {child_node.get("name")}'})
        
    except Exception as e:
        logger.error(f"Error processing kill_process request: {e}")
        return web.json_response({'error': str(e)}, status=500)

#########################################################################
# main function
#########################################################################
if __name__ == "__main__":
    # Add routes and configure CORS for each route
    login_route = app.router.add_post('/login', login)
    nodes_route = app.router.add_get('/api/nodes', fetch_nodes)
    kill_process_route = app.router.add_post('/api/killprocess', kill_process)
    
    # Add CORS to API routes
    cors.add(login_route)
    cors.add(nodes_route)
    cors.add(kill_process_route)
    
    # Static file routes (no CORS needed)
    app.router.add_static('/static/',
                          path=os.path.join(constants.basedir, '..', 'frontend', 'build', 'static'),
                          name='static')
    app.router.add_get('/', serve_static_file)
    app.router.add_get('/{path:.*}', serve_static_file)

    logger.info("starting incident agent...")
    web.run_app(app, port=8080)
