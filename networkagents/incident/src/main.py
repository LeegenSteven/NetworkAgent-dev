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

import asyncio
from aiohttp import web
import aiohttp_cors
import logging
import os
import aiohttp
from utils.nodes import get_nodes
from utils.a2a_client import EngineerAgentClient

log_format = "%(asctime)s::%(levelname)s::%(name)s::"             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

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

async def serve_static_file(request):
    return web.FileResponse(os.path.join(BASE_DIR, '..', 'frontend', 'build', 'index.html'))

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

async def get_agents(request):
    logger.info("get agents")

    supervisor_url = os.environ.get('SUPERVISOR_URL')
    if not supervisor_url:
        return web.json_response({'error': 'SUPERVISOR_URL not set'}, status=500)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{supervisor_url}/listagents") as response:
                if response.status == 200:
                    agents = await response.json()
                    return web.json_response(agents)
                else:
                    return web.json_response({'error': 'Failed to fetch agents from supervisor'}, status=response.status)
    except Exception as e:
        logger.error(f"Error fetching agents: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def fetch_nodes(request):
    logger.info("get nodes")

    nodes,success=get_nodes()
    if success:
        return web.json_response(nodes)
    else:
        return web.json_response([])


async def start_task(request):
    data = await request.json()
    agent_name = data.get('agentName')
    agent_url = data.get('agentUrl')
    node_name = data.get('nodeName')
    objective = data.get('objective')
    additional_info = data.get('tableData')

    logger.info("start task with %s %s %s %s %s", node_name, agent_name, agent_url, objective, additional_info)

    client = EngineerAgentClient(agent_url)
    task_id = await client.send_task(objective)

    return web.json_response({'task_id': task_id})


async def get_task_status(request):
    id = request.match_info.get('id', "Unknown")
    agent_url = request.query.get('agentUrl')
    logger.info("get status for task %s", id)

    if not agent_url:
        return web.json_response({'error': 'agentUrl not provided'}, status=400)

    client = EngineerAgentClient(agent_url)
    status = await client.get_task_status(id)

    return web.json_response({'task_id': id, 'status': status.value})


if __name__ == "__main__":
    app.router.add_post('/login', login)
    app.router.add_get('/api/agents', get_agents)
    app.router.add_get('/api/nodes', fetch_nodes)
    app.router.add_post('/api/start_task', start_task)
    app.router.add_get('/api/task/{id}', get_task_status)
    app.router.add_static('/static/',
                          path=os.path.join(BASE_DIR, '..', 'frontend', 'build', 'static'),
                          name='static')
    app.router.add_get('/', serve_static_file)
    app.router.add_get('/{path:.*}', serve_static_file)

    logger.info("starting incident agent...")
    web.run_app(app, port=8080)
