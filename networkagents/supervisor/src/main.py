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
from tools.topology import build_graph, spanner_connect
from tools.metrics import fetch_all_last_metrics
from tools.logs import fetch_log_entries
from endpoints.socketendpoint import clients_state, view_to_edge_label_map


log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

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

async def send_topology_updates():
    """Background task that periodically sends topology and logs updates to all connected clients."""
    database = spanner_connect()
    while True:
        try:
            # For each connected client send logs if enabled
            for sid, state in clients_state.items():
                # Build the graph for the requested view 
                edge_label = view_to_edge_label_map[state['topology']['view']]
                elements, success = build_graph(database, edge_label)

                # Send topology update to client sid
                await sio.emit('topology_update', {'elements': elements})
                logger.debug("Sent topology update to all clients")
                
                if not success:
                    logger.error("Failed to build graph for topology update")

                if ('logs' in state) and state['logs']['enabled']:
                    try:
                        # Fetch logs
                        logs = fetch_log_entries()
                        
                        # Also send a separate logs update
                        await sio.emit('logs_update', logs, room=sid)
                        
                        logger.info(f"Sent topology update with logs to client {sid}")
                    except Exception as e:
                        logger.error(f"Error sending logs to client {sid}: {e}")

                # Send all last metrics to client sid
                metrics = fetch_all_last_metrics()
                # print("========================\n", json.dumps(metrics, indent=2), "\n===========================")
                await sio.emit('all_last_metrics_update', metrics, room=sid)
                
        except Exception as e:
            logger.error(f"Error in topology update task: {e}")
        
        # Wait for 5 seconds before sending the next update
        await asyncio.sleep(5)

async def init():
    runner = web.AppRunner(app)
    await runner.setup()

    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 9000

    logger.info("starting server on port %s",port)
    site = web.TCPSite(runner, host="0.0.0.0", port=port, ssl_context=None)
    await site.start()
    
    # Start the background task for topology updates
    asyncio.create_task(send_topology_updates())

if __name__ == "__main__":
    logger.info("starting network agent...")
    
    import endpoints
    socketEndpoint = endpoints.SocketEndpoint(sio)
    restEndpoint = endpoints.RestEndpoint(app, cors)

    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init())
    loop.run_forever()
