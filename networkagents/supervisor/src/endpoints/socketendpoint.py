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

import logging
import datetime
import json
from agent.host_agent import HostAgent
from tools.topology import fetch_db_node, build_graph, spanner_connect
from tools.logs import fetch_log_entries, delete_logs
from tools.metrics import *
from utils.error_handler import (
    SupervisorAgentError,
    RemoteAgentError,
    ToolError,
    ErrorSeverity,
    send_error_message
)

logger = logging.getLogger(__name__)

# Dictionary to track clients that have requested logs
clients_state = {}
view_to_edge_label_map = {'network': 'isConnectedTo', 'resources': 'Manages', 'both': None}

class SocketEndpoint:
    """
    Socket.IO endpoint for handling client connections.    
    """
    def __init__(self, sio):
        logger.info("SocketEndpoint init")
        self.sio = sio
        self.callbacks()

    def callbacks(self):
        @self.sio.event
        async def connect(sid, environ, auth):
            logger.info("connected client %s", sid)

        @self.sio.event
        async def chat_message(sid, data):
            logger.info("chat message from %s: %s", sid, data.get('text', ''))

            agent=await HostAgent.get_instance()
            await agent.run(data['text'], self.sio, sid)

        @self.sio.event
        async def add_remote_agent(sid, data):
            logger.info(f"add remote agent {sid}: {data}")
            
            try:
                # Validate the data
                if not data or 'url' not in data:
                    logger.error(f"Invalid data received for add_remote_agent: {data}")
                    error = SupervisorAgentError(
                        message="Invalid data: URL is required",
                        severity=ErrorSeverity.ERROR
                    )
                    await send_error_message(self.sio, sid, error)
                    return
                    
                agent = await HostAgent.get_instance()
                
                try:
                    response = await agent.add_remote_agent(data.get('url'))
                    if response:
                        logger.info(f"Successfully added agent: {response}")
                        remote_agents = agent.list_remote_agents()
                        await self.sio.emit('update_remote_agents', remote_agents, room=sid)
                        logger.info(f"Sent list_remote_agents to {sid}")
                except SupervisorAgentError as e:
                    # SupervisorAgentError instances are already handled by the host_agent
                    # Just log that we caught it here
                    logger.error(f"Error adding remote agent: {e.message}")
                    await send_error_message(self.sio, sid, e)
            except Exception as e:
                logger.error(f"Unexpected error in add_remote_agent: {str(e)}", exc_info=True)
                error = SupervisorAgentError(
                    message=f"Unexpected error adding remote agent: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio, sid, error)

        @self.sio.event
        async def delete_remote_agent(sid, data):
            logger.info(f"delete remote agent for {sid}: {data}")
            agent = await HostAgent.get_instance()
            
            # Get the URL of the agent to delete
            url = data.get('url')
            success = await agent.delete_remote_agent(url)
            remote_agents = agent.list_remote_agents()
            await self.sio.emit('update_remote_agents', remote_agents, room=sid)
            logger.info(f"Sent list_remote_agents to {sid}")

        @self.sio.event
        async def get_node_details(sid, data):
            logger.info("get node details for %s %s", sid, data)          
            try:
                node_id = data.get('id')
                if not node_id:
                    logger.error("No node ID provided in get_node_details event")
                    error = SupervisorAgentError(
                        message="No node ID provided",
                        severity=ErrorSeverity.ERROR
                    )
                    await send_error_message(self.sio, sid, error)
                    return
                    
                try:
                    # Fetch node details from the database
                    node_data = fetch_db_node(node_id)
                    if node_data:
                        # Extract node details
                        id, kind, name, display_name, status, properties = node_data
                        
                        # Parse the JSON properties
                        import json
                        try:
                            properties_dict = json.loads(properties)
                        except json.JSONDecodeError:
                            properties_dict = {}
                        
                        # Create response with node details
                        response = {
                            'id': id,
                            'kind': kind,
                            'name': name,
                            'display_name': display_name,
                            'status': status,
                            'properties': properties_dict
                        }
                        
                        # Send node details back to the client
                        await self.sio.emit('node_details_response', response, room=sid)
                    else:
                        logger.error(f"Node with ID {node_id} not found")
                        error = SupervisorAgentError(
                            message=f"Node with ID {node_id} not found",
                            severity=ErrorSeverity.ERROR
                        )
                        await send_error_message(self.sio, sid, error)
                        await self.sio.emit('node_details_response', {'error': f"Node with ID {node_id} not found"}, room=sid)
                except Exception as e:
                    logger.error(f"Error fetching node details: {e}")
                    error = SupervisorAgentError(
                        message=f"Error fetching node details: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                    await send_error_message(self.sio, sid, error)
                    await self.sio.emit('node_details_response', {'error': f"Error fetching node details: {str(e)}"}, room=sid)
            except Exception as e:
                logger.error(f"Unexpected error in get_node_details: {str(e)}", exc_info=True)
                error = SupervisorAgentError(
                    message=f"Unexpected error: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio, sid, error)

        @self.sio.event
        async def get_topology(sid, data):
            logger.info(f"get_topology for {sid}: {data}")
            try:
                # Connect to the database
                database = spanner_connect()

                # Update the client topology preferences
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['topology'] = data

                # map dashboard view dropdown menu entries to graph labels
                edge_label = view_to_edge_label_map[data['view']] 
                
                # Build the graph with selected edge label
                elements, success = build_graph(database, edge_label)
                
                if success:
                    # Prepare response
                    response = {'elements': elements}
                    
                    # Send topology update to the client
                    await self.sio.emit('topology_update', response, room=sid)
                    logger.info(f"Sent topology update to {sid} with {len(elements)} elements for '{data['view']}' view")
                else:
                    logger.error(f"Failed to build graph for client {sid}")
                    error = SupervisorAgentError(
                        message="Failed to build graph",
                        severity=ErrorSeverity.ERROR
                    )
                    await send_error_message(self.sio, sid, error)
                    await self.sio.emit('topology_update', {'error': "Failed to build graph"}, room=sid)
            except Exception as e:
                logger.error(f"Error fetching topology: {e}")
                error = SupervisorAgentError(
                    message=f"Error fetching topology: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio, sid, error)
                await self.sio.emit('topology_update', {'error': f"Error fetching topology: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def get_logs(sid, data):
            logger.info(f"get_logs for {sid}: {data}")
            try:                
                # Update the client's log preference
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['logs'] = data
                
                enabled = clients_state[sid]['logs']['enabled']
                if enabled:
                    # Fetch and send initial logs
                    logs = fetch_log_entries()
                    await self.sio.emit('logs_update', logs, room=sid)
                    logger.info(f"Sent initial logs to {sid}")
                
                logger.info(f"Logs {'enabled' if enabled else 'disabled'} for {sid}")
            except Exception as e:
                logger.error(f"Error handling get_logs: {e}")
                await self.sio.emit('logs_update', {'error': f"Error fetching logs: {str(e)}"}, room=sid)

        @self.sio.event
        async def reset_logs(sid):
            logger.info(f"reset_logs for {sid}")
            try:
                success = delete_logs()
                if success:
                    logs = []
                    await self.sio.emit('logs_update', logs, room=sid)
                    logger.info(f"Sent empty logs after reset to {sid}")
                else:
                    raise Exception("Logs deletion in database failed.")
            except Exception as e:
                logger.error(f"Error handling reset_logs: {e}")
                await self.sio.emit('logs_update', {'error': f"Error resetting logs: {str(e)}"}, room=sid)

        @self.sio.event
        async def reset_chat(sid, data):
            """
            reset the conversation history in the agent
            """
            logger.info("reset chat for %s", sid)
            
            try:
                # Get the NetworkAgent instance and reset its conversation history
                agent = await HostAgent.get_instance()
                await agent.reset_conversation(sid)
                
                # Send a confirmation message to the client
                welcome_msg = {
                    'id': f'welcome-{sid}-reset',
                    'text': 'How can I help you?',
                    'isUser': False,
                    'timestamp': datetime.datetime.now().isoformat()
                }
                await self.sio.emit('chat_message', welcome_msg, room=sid)
                logger.info(f"Chat reset for {sid}")
            except Exception as e:
                logger.error(f"Error resetting chat: {e}")
                error = SupervisorAgentError(
                    message=f"Error resetting chat: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio, sid, error)
            
        @self.sio.event
        async def disconnect(sid):
            logger.info("disconnected from %s", sid)
            
            # Remove client from logs tracking
            if sid in clients_state:
                del clients_state[sid]

        @self.sio.event
        async def get_all_last_metrics(sid):
            logger.info(f"get_all_last_metrics for {sid}")
            try:                
                metrics = fetch_all_last_metrics()
                await self.sio.emit('all_last_metrics_update', metrics, room=sid)
                logger.info(f"Sent all_last_metrics_update to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_last_metrics: {e}")
                await self.sio.emit('all_last_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_all_metrics(sid):
            logger.info(f"get_all_metrics for {sid}")
            try:                
                metrics = fetch_all_metrics()
                await self.sio.emit('all_metrics_update', metrics, room=sid)
                logger.info(f"Sent all_metrics_update to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_metrics: {e}")
                await self.sio.emit('all_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_last_metrics_for_id(sid, data):
            logger.info(f"get_last_metrics_for_id for {sid}: {data}")
            try:                
                metrics = fetch_last_metrics_for_id(data['id'])
                await self.sio.emit('last_metrics_update_for_id', metrics, room=sid)
                logger.info(f"Sent last_metrics_update_for_id logs to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_last_metrics_for_id: {e}")
                await self.sio.emit('last_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_all_metrics_for_id(sid, data):
            logger.info(f"get_all_metrics_for_id for {sid}: {data}")
            try:                
                metrics = fetch_all_metrics_for_id(data['id'])
                await self.sio.emit('all_metrics_update_for_id', metrics, room=sid)
                logger.info(f"Sent all_metrics_update_for_id logs to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_metrics_for_id: {e}")
                await self.sio.emit('all_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def reset_metrics(sid):
            logger.info(f"reset_metrics for {sid}")
            try:
                success = clear_network_metrics()
                if success:
                    # Send empty metrics updates to the client
                    await self.sio.emit('all_last_metrics_update', {}, room=sid)
                    await self.sio.emit('all_metrics_update', {}, room=sid)
                    logger.info(f"Sent empty metrics after reset to {sid}")
                else:
                    raise Exception("Metrics deletion in database failed.")
            except Exception as e:
                logger.error(f"Error handling reset_metrics: {e}")
                await self.sio.emit('all_metrics_update', {'error': f"Error resetting metrics: {str(e)}"}, room=sid)
