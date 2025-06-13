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

    _instance = None

    def __init__(self, sio):
        logger.info("SocketEndpoint init")

        SocketEndpoint._instance = self

        self.sio = sio
        self.callbacks()

    async def sendPushNotification(self, data):
        """
        Send data to all connected clients
        
        Args:
            data: The data to send to all connected clients
            
        Returns:
            bool: True if the data was sent successfully, False otherwise
        """
        try:
            logger.info("Sending %s to all connected clients", data)
            # Emit a 'push_notification' event to all connected clients
            await self.sio.emit('push_notification', data)
            return True
        except Exception as e:
            logger.error(f"Error sending data to all clients: {str(e)}", exc_info=True)
            return False


    def callbacks(self):
        @self.sio.event
        async def connect(sid, environ, auth):
            logger.info("connected client %s", sid)

            # add sio to the agent
            agent=await HostAgent.get_instance()
            agent.sio_sessions[sid]=self.sio
            logger.info(agent.sio_sessions)

        @self.sio.event
        async def chat_message(sid, data):
            logger.info("chat message from %s: %s", sid, data.get('text', ''))

            agent=await HostAgent.get_instance()
            await agent.run(data['text'], self.sio, sid)

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
                await agent.reset_conversation()
                
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

            # remove the sid/sio from the agent session
            agent=await HostAgent.get_instance()
            if sid in agent.sio_sessions:
                del agent.sio_sessions[sid]
            logger.info(agent.sio_sessions)

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
                
        @self.sio.event
        async def notification_feedback(sid, data):
            """
            Handle notification feedback from the dashboard UI (thumbs up/down)
            
            Args:
                sid: The session ID of the client
                data: The feedback data containing notification details and feedback type
            """
            try:
                notification_id = data.get('notification_id')
                feedback = data.get('feedback')  # 'approve' or 'reject'
                notification_details = data.get('notification_details', {})
                
                # Log the feedback event
                logger.info(f"Received notification feedback from {sid}: {feedback} for notification {notification_id}")
                logger.info(f"Notification details: {notification_details}")
                
                agent = await HostAgent.get_instance()
                # send the approval
                await agent.sendApproval(notification_details['name'],feedback, notification_details['task_id'], notification_details['context_id'])

            except Exception as e:
                logger.error(f"Error handling notification feedback: {e}")
