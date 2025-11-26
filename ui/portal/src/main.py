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
import socketio
from aiohttp import web
import aiohttp_cors
import logging
import os
from agent.client import send_order

log_format = "%(asctime)s::%(levelname)s::%(name)s::"             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# get the base directory for this file
basedir=os.path.dirname(os.path.realpath(__file__))
logger.info("Base directory is %s", basedir)

# Initialize Socket.IO server with CORS enabled for all origins
sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False
)

# Initialize aiohttp application with no middleware
app = web.Application()
sio.attach(app)

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
# SocketIO Handlers
#########################################################################
@sio.event
async def connect(sid, environ):
    logger.info("connected %s", sid)

@sio.event
async def disconnect(sid):
    logger.info("disconnected %s", sid)

#########################################################################
# return location of react ui
#########################################################################
async def serve_static_file(request):
    return web.FileResponse(os.path.join(basedir, '..', 'frontend', 'build', 'index.html'))

async def serve_root_files(request):
    filename = request.match_info['filename']
    filepath = os.path.join(basedir, '..', 'frontend', 'build', filename)
    if os.path.exists(filepath):
        return web.FileResponse(filepath)
    raise web.HTTPNotFound()

#########################################################################
# Place Order
#########################################################################
async def placeOrder(request):
    logger.info("order received")

    data = await request.json()
    logger.info(data)
    
    # will send a2a request and yield status events which update the UI in some way
    await send_order(data)

    return web.json_response({'success': True})

#########################################################################
# Login
#########################################################################
async def login(request):
    logger.info("login request received")
    data = await request.json()
    username = data.get('username')
    password = data.get('password')

    # Retrieve credentials from environment variables
    app_user = os.environ.get('WEBAPPS_LOGIN', 'admin')
    app_password = os.environ.get('WEBAPPS_PWD', 'password')

    logger.info(f"Authenticating with user: {app_user} and password: {app_password}")

    if username == app_user and password == app_password:
        return web.json_response({'success': True, 'user': {'username': username}})
    else:
        return web.json_response({'success': False, 'message': 'Invalid credentials'}, status=401)

#########################################################################
# Init everything
#########################################################################
async def init():

    # Add routes and configure CORS for each route
    order_route = app.router.add_post('/order', placeOrder)
    cors.add(order_route)

    login_route = app.router.add_post('/login', login)
    cors.add(login_route)

    # Static file routes (no CORS needed)
    app.router.add_static('/static/',
                          path=os.path.join(basedir, '..', 'frontend', 'build', 'static'),
                          name='static')
    app.router.add_get(
        '/{filename:(asset-manifest.json|dt-icon.png|vodafone-icon.png|o2-icon.png|favicon.ico|logo192.png|logo512.png|manifest.json|robots.txt)}',
        serve_root_files,
    )
    app.router.add_get('/', serve_static_file)
    app.router.add_get('/{path:.*}', serve_static_file)

    runner = web.AppRunner(app)
    await runner.setup()

    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 9080

    logger.info("starting server on port %s",port)
    site = web.TCPSite(runner, host="0.0.0.0", port=port, ssl_context=None)
    await site.start()

#########################################################################
# main function
#########################################################################
if __name__ == "__main__":
    logger.info("starting portal...")
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init())
    loop.run_forever()
