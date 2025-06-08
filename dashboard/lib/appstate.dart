import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'models/chat_message.dart';
import 'models/agent.dart';
import 'models/network_node.dart';
import 'models/log_entry.dart';
import 'models/metrics.dart';
import 'models/push_notification.dart';
import 'utils/environment_config.dart';
import 'utils/APIService.dart';
import 'widgets/network_topology.dart';

class Appstate extends ChangeNotifier {
  // Chat state
  final List<ChatMessage> _chatMessages = [];
  io.Socket? _socket;
  
  // API Service
  final APIService _apiService = APIService();
  
  // Agents state
  final List<Agent> _agents = [];
  
  // Network topology state
  NetworkTopology _topology = NetworkTopology.empty();
  bool _isConnected = false;
  bool _hasReceivedTopology = false;
  
  // Topology filtering state
  bool _filterByNodeType = false;
  Set<NodeType> _selectedNodeTypes = Set<NodeType>.from(NodeType.values);
  
  // Topology layout state
  String _selectedTopologyLayout = 'force-directed';
  
  // Log widget state
  List<LogEntry> _logs = [];
  bool _isLoadingLogs = false;
  
  // Metrics state
  Metrics _metrics = Metrics({});
  bool _isLoadingMetrics = false;
  
  // Push notifications state
  final List<PushNotification> _pushNotifications = [];
  
  // Getters
  List<ChatMessage> get chatMessages => _chatMessages;
  io.Socket? get socket => _socket;
  List<Agent> get agents => List.unmodifiable(_agents);
  NetworkTopology get topology => _topology;
  bool get isConnected => _isConnected;
  bool get hasReceivedTopology => _hasReceivedTopology;
  bool get filterByNodeType => _filterByNodeType;
  Set<NodeType> get selectedNodeTypes => Set<NodeType>.from(_selectedNodeTypes);
  String get selectedTopologyLayout => _selectedTopologyLayout;
  List<LogEntry> get logs => _logs;
  bool get isLoadingLogs => _isLoadingLogs;
  Metrics get metrics => _metrics;
  bool get isLoadingMetrics => _isLoadingMetrics;
  List<PushNotification> get pushNotifications => List.unmodifiable(_pushNotifications);
  
  Appstate() {
    _connectToServer();
  }
  
  // Connect to the server and initialize socket
  void _connectToServer() {
    // Connect to the NetworkAgent socket server
    _socket = io.io(EnvironmentConfig.agentUrl, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': true,
    });
    
    _socket!.onConnect((_) async {
      print('Connected to NetworkAgent server');
      _isConnected = true;
      
      // Request initial topology data when connected
      _socket!.emit('get_topology', {'view': NetworkTopologyWidget.defaultView});
      
      // Initialize the list of remote agents from REST API
      try {
        final agents = await _apiService.listAgents();
        if (agents.isNotEmpty) {
          _agents.clear();
          _agents.addAll(agents);
          print('Initialized ${agents.length} remote agents from REST API');
        }
      } catch (e) {
        print('Error initializing remote agents: $e');
      }
      
      notifyListeners();
    });
    
    _socket!.onDisconnect((_) {
      print('Disconnected from NetworkAgent server');
      _isConnected = false;
      
      // Reset remote agents when socket disconnects
      if (_agents.isNotEmpty) {
        print('Resetting remote agents due to socket disconnection');
        _agents.clear();
      }
      
      notifyListeners();
    });
    
    // Listen for chat messages from the server
    _socket!.on('chat_message', (data) {
      if (data != null) {
        final message = ChatMessage(
          id: data['id'] ?? DateTime.now().millisecondsSinceEpoch.toString(),
          text: data['text'] ?? '',
          isUser: data['isUser'] ?? false,
          timestamp: data['timestamp'] != null 
              ? DateTime.parse(data['timestamp']) 
              : DateTime.now(),
        );
        addChatMessage(message);
      }
    });

    // Agent management has been moved to REST endpoints
    
    // Listen for topology updates
    _socket!.on('topology_update', (data) {
      if (data != null && data['elements'] != null) {
        print('Received topology update with ${data['elements'].length} elements');
        _updateTopology(data['elements']);
      }
      
      // If logs are enabled and logs data is included, update logs
      if (data != null && data['logs'] != null) {
        _updateLogs(data['logs']);
      }
    });
    
    // Listen for log updates
    _socket!.on('logs_update', (data) {
      if (data != null) {
        _updateLogs(data);
      }
    });

    // Listen for metrics updates
    _socket!.on('all_last_metrics_update', (data) {
      if (data != null) {
        _updateMetrics(data);        
      }
    });
    
    // Listen for push notifications from supervisor agent
    _socket!.on('push_notification', (data) {
      if (data != null) {
        _addPushNotification(data);
      }
    });
    
    _socket!.connect();
  }
  
  // Set the socket connection (legacy method, kept for compatibility)
  void setSocket(io.Socket socket) {
    // This method is kept for backward compatibility but doesn't do anything
    // since the socket is now initialized in the constructor
  }
  
  // Add a chat message
  void addChatMessage(ChatMessage message) {
    _chatMessages.add(message);
    notifyListeners();
  }
  
  // Add a new chat message with text
  void addChatMessageWithText(String text, bool isUser) {
    final message = ChatMessage(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      text: text,
      isUser: isUser,
      timestamp: DateTime.now(),
    );
    addChatMessage(message);
  }
  
  // Send a message to the server
  Future<void> sendChatMessage(String text) async {
    if (text.trim().isEmpty) return;
    
    // Add user message to local state
    addChatMessageWithText(text, true);
    
    // Send message to server if socket is connected
    if (_socket != null && _socket!.connected) {
      _socket!.emit('chat_message', {'text': text});
    }
  }
  
  // Reset chat history
  void resetChat() {
    _chatMessages.clear();
    
    // Send reset_chat event to server if socket is connected
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_chat', {});
    }
    
    notifyListeners();
  }
  
  // Reset chat history with socket disconnect and reconnect
  void resetChatWithSocketReset() {
    // Clear chat messages
    _chatMessages.clear();
    
    // Disconnect and reconnect the socket if it exists
    if (_socket != null) {
      print('Resetting connection: disconnecting socket');
      
      // Disconnect the socket
      _socket!.disconnect();
      
      // Reconnect after a short delay
      Future.delayed(const Duration(milliseconds: 500), () {
        print('Resetting connection: reconnecting socket');
        _socket!.connect();
      });
    }
    
    notifyListeners();
  }
  
  // Agent management methods
  Future<void> addAgent(String url) async {
    try {
      print('Adding agent with URL: $url');
      
      // Call the REST API to add the agent
      final agent = await _apiService.addAgent(url);
      
      if (agent != null) {
        // Add the new agent to the list
        _agents.add(agent);
        print('Successfully added agent: ${agent.name}');
        
        // Refresh the full list to ensure consistency
        final agents = await _apiService.listAgents();
        if (agents.isNotEmpty) {
          _agents.clear();
          _agents.addAll(agents);
          print('Updated agents list with ${agents.length} agents');
        }
        
        // Notify listeners about the state change
        notifyListeners();
      } else {
        print('Failed to add agent with URL: $url');
      }
    } catch (e) {
      print('Error adding agent: $e');
    }
  }
  
  Future<void> removeAgent(String id) async {
    try {
      // Find the agent to get its URL
      Agent? agentToRemove = _agents.firstWhere((a) => a.id == id);
      print('Removing agent with ID: $id, URL: ${agentToRemove.url}');
      
      // Call the REST API to delete the agent
      final updatedAgents = await _apiService.deleteAgent(agentToRemove.url);
      
      // Update the local agents list
      _agents.clear();
      _agents.addAll(updatedAgents);
      print('Updated agents list with ${updatedAgents.length} agents');
      
      // Notify listeners about the state change
      notifyListeners();
    } catch (e) {
      print('Error removing agent: $e');
    }
  }
  
  // Update topology filtering
  void updateTopologyFiltering({
    required bool filterByNodeType,
    required Set<NodeType> selectedNodeTypes,
  }) {
    _filterByNodeType = filterByNodeType;
    _selectedNodeTypes = Set<NodeType>.from(selectedNodeTypes);
    notifyListeners();
  }
  
  // Update topology layout
  void updateTopologyLayout(String layout) {
    _selectedTopologyLayout = layout;
    notifyListeners();
  }
  
  // Update topology from server data
  void _updateTopology(List<dynamic> elements) {
    try {
      // Process the elements from the server and convert to NetworkTopology
      final nodes = <NetworkNode>[];
      final connections = <NetworkConnection>[];
      final nodeIds = <String>{};
      
      // First pass: collect all nodes
      for (var element in elements) {
        if (element['group'] == 'nodes') {
          final data = element['data'];
          if (data == null || data['id'] == null) {
            print('Warning: Skipping node with missing data or ID');
            continue;
          }
          
          final id = data['id'];
          final name = data['name'] ?? 'Unknown';
          final kind = data['kind'] ?? '';
          final status = data['status'] ?? '';
          
          // Map the kind to a NodeType
          NodeType type = NetworkNode.mapKindToNodeType(kind);
          
          nodes.add(NetworkNode(
            id: id,
            name: name,
            type: type,
            properties: {
              'kind': kind,
              'status': status,
              'ip': data['ip'] ?? '',
            },
          ));
          
          // Keep track of valid node IDs
          nodeIds.add(id);
        }
      }
      
      // Second pass: collect all edges - only for nodes that exist
      int connectionId = 1;
      for (var element in elements) {
        if (element['group'] == 'edges') {
          final data = element['data'];
          if (data == null || data['source'] == null || data['target'] == null) {
            print('Warning: Skipping edge with missing data, source, or target');
            continue;
          }
          
          final sourceId = data['source'];
          final targetId = data['target'];
          
          // Skip edges where either source or target node doesn't exist
          if (!nodeIds.contains(sourceId) || !nodeIds.contains(targetId)) {
            print('Warning: Skipping edge with non-existent source or target: $sourceId -> $targetId');
            continue;
          }
          
          final label = data['label'] ?? '';
          
          connections.add(NetworkConnection(
            id: 'c${connectionId++}',
            sourceId: sourceId,
            targetId: targetId,
            label: label,
            properties: {
              'src_kind': data['src_kind'] ?? '',
              'tgt_kind': data['tgt_kind'] ?? '',
            },
          ));
        }
      }
      
      // Update the state with the new topology
      _topology = NetworkTopology(nodes: nodes, connections: connections);
      _hasReceivedTopology = true;
      notifyListeners();
    } catch (e) {
      print('Error updating topology: $e');
      // If there's an error, create an empty topology to avoid crashes
      _topology = NetworkTopology.empty();
      _hasReceivedTopology = true;
      notifyListeners();
    }
  }
  
  // Update logs from server data
  void _updateLogs(dynamic logsData) {
    try {
      List<LogEntry> newLogs = [];
      
      if (logsData is List) {
        // Convert each log entry from JSON to LogEntry object
        newLogs = logsData.map((logData) => 
          logData is Map<String, dynamic> 
            ? LogEntry.fromJson(logData)
            : LogEntry(
                timestamp: DateTime.now().toIso8601String(),
                level: 'INFO',
                message: logData.toString(),
                source: 'unknown',
              )
        ).toList();
      } else if (logsData != null) {
        // Handle any unexpected format
        print('Unexpected log data format: ${logsData.runtimeType}');
        newLogs.add(LogEntry(
          timestamp: DateTime.now().toIso8601String(),
          level: 'WARNING',
          message: 'Received logs in unexpected format: ${logsData.runtimeType}',
          source: 'dashboard',
        ));
      }
      
      _logs = newLogs;
      _isLoadingLogs = false;
      notifyListeners();
    } catch (e) {
      print('Error updating logs: $e');
      _isLoadingLogs = false;
      notifyListeners();
    }
  }
  
  // Toggle logs visibility
  void toggleLogs(bool showLogs) {
    _isLoadingLogs = showLogs;
    
    if (_socket != null && _socket!.connected) {
      if (showLogs) {
        // Request logs from server
        _socket!.emit('get_logs', {'enabled': true});
      } else {
        // Notify server to stop sending logs
        _socket!.emit('get_logs', {'enabled': false});
      }
    }
    
    notifyListeners();
  }
  
  // Reset logs
  void resetLogs() {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_logs');
    }
  }
  
  // Update metrics from server data
  void _updateMetrics(dynamic metricsData) {
    try {
      // Use the Metrics class to parse the metrics data
      final metrics = Metrics.fromJson(metricsData);
      
      _metrics = metrics;
      _isLoadingMetrics = false;
      notifyListeners();
    } catch (e) {
      print('Error updating metrics: $e');
      _isLoadingMetrics = false;
      notifyListeners();
    }
  }
  
  // Reset metrics
  void resetMetrics() {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_metrics');
    }
  }
  
  // Get node details
  void getNodeDetails(String nodeId) {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_node_details', {'id': nodeId});
    }
  }
  
  // Get topology view
  void getTopologyView(String view) {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_topology', {'view': view});
    }
  }
  
  // Add a push notification
  void _addPushNotification(dynamic data) {
    try {
      final notification = PushNotification.fromJson(data);
      _pushNotifications.add(notification);
      notifyListeners();
    } catch (e) {
      print('Error adding push notification: $e');
    }
  }
  
  // Mark a push notification as read
  void markNotificationAsRead(String id) {
    final index = _pushNotifications.indexWhere((notification) => notification.id == id);
    if (index != -1) {
      final notification = _pushNotifications[index];
      final updatedNotification = notification.copyWith(isRead: true);
      _pushNotifications[index] = updatedNotification;
      notifyListeners();
    }
  }
  
  // Clear all push notifications
  void clearAllNotifications() {
    _pushNotifications.clear();
    notifyListeners();
  }
  
  // Remove a specific notification by ID
  void removeNotification(String id) {
    final index = _pushNotifications.indexWhere((notification) => notification.id == id);
    if (index != -1) {
      _pushNotifications.removeAt(index);
      notifyListeners();
    }
  }
  
  @override
  void dispose() {
    // Remove event listeners to prevent memory leaks
    if (_socket != null) {
      _socket!.off('chat_message');
      // Agent management has been moved to REST endpoints
      _socket!.off('topology_update');
      _socket!.off('logs_update');
      _socket!.off('all_last_metrics_update');
      _socket!.off('push_notification');
      _socket!.disconnect();
    }
    super.dispose();
  }
}
