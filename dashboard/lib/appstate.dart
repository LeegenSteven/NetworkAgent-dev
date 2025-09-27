import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'models/agent.dart';
import 'models/network_node.dart';
import 'models/log_entry.dart';
import 'models/metrics.dart';
import 'models/push_notification.dart';
import 'models/incident.dart';
import 'utils/environment_config.dart';
import 'utils/APIService.dart';
import 'widgets/topology/network_topology.dart';

class Appstate extends ChangeNotifier {
  // Socket connection
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
  
  // Topology view state
  String _selectedTopologyView = NetworkTopologyWidget.defaultView;
  
  // Log widget state
  List<LogEntry> _logs = [];
  bool _isLoadingLogs = false;
  
  // Metrics state
  Metrics _metrics = Metrics({});
  bool _isLoadingMetrics = false;
  
  // Performance graph widget state
  bool _showPerformanceGraph = false;
  
  // Push notifications state
  final List<PushNotification> _pushNotifications = [];
  
  // Incidents state
  final List<Incident> _incidents = [];
  bool _isLoadingIncidents = false;
  
  // Getters
  io.Socket? get socket => _socket;
  List<Agent> get agents => List.unmodifiable(_agents);
  NetworkTopology get topology => _topology;
  bool get isConnected => _isConnected;
  bool get hasReceivedTopology => _hasReceivedTopology;
  bool get filterByNodeType => _filterByNodeType;
  Set<NodeType> get selectedNodeTypes => Set<NodeType>.from(_selectedNodeTypes);
  String get selectedTopologyLayout => _selectedTopologyLayout;
  String get selectedTopologyView => _selectedTopologyView;
  List<LogEntry> get logs => _logs;
  bool get isLoadingLogs => _isLoadingLogs;
  Metrics get metrics => _metrics;
  bool get isLoadingMetrics => _isLoadingMetrics;
  bool get showPerformanceGraph => _showPerformanceGraph;
  List<PushNotification> get pushNotifications => List.unmodifiable(_pushNotifications);
  List<Incident> get incidents => List.unmodifiable(_incidents);
  bool get isLoadingIncidents => _isLoadingIncidents;
  
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
      
      // Initialize incidents from REST API on startup
      try {
        await _fetchIncidents();
      } catch (e) {
        print('Error initializing incidents on startup: $e');
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
    _socket!.on('metrics_update', (data) {
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
  
  // resetChat method removed - AG-UI chat panel manages thread IDs directly
  
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
  
  // Callback for topology widget rebuild
  VoidCallback? _topologyRebuildCallback;
  
  // Set the topology rebuild callback
  void setTopologyRebuildCallback(VoidCallback callback) {
    _topologyRebuildCallback = callback;
  }
  
  // Update topology filtering
  void updateTopologyFiltering({
    required bool filterByNodeType,
    required Set<NodeType> selectedNodeTypes,
  }) {
    _filterByNodeType = filterByNodeType;
    _selectedNodeTypes = Set<NodeType>.from(selectedNodeTypes);
    
    // Trigger topology widget rebuild directly instead of notifyListeners
    if (_topologyRebuildCallback != null) {
      _topologyRebuildCallback!();
    }
  }
  
  // Update topology layout
  void updateTopologyLayout(String layout) {
    _selectedTopologyLayout = layout;
    
    // Trigger topology widget rebuild directly instead of notifyListeners
    if (_topologyRebuildCallback != null) {
      _topologyRebuildCallback!();
    }
  }
  
  // Helper method to check if two topologies are equivalent
  bool _areTopologiesEquivalent(List<NetworkNode> nodes1, List<NetworkConnection> connections1,
                               List<NetworkNode> nodes2, List<NetworkConnection> connections2) {
    // Check if the number of nodes and connections are the same
    if (nodes1.length != nodes2.length || connections1.length != connections2.length) {
      // print('Topology comparison: Different lengths');
      return false;
    }
    
    // Create maps of nodes by ID for more efficient comparison
    final nodesMap1 = <String, NetworkNode>{};
    final nodesMap2 = <String, NetworkNode>{};
    
    for (var node in nodes1) {
      nodesMap1[node.id] = node;
    }
    
    for (var node in nodes2) {
      nodesMap2[node.id] = node;
    }
    
    // Check if both topologies have the same node IDs
    if (!nodesMap1.keys.toSet().containsAll(nodesMap2.keys) ||
        !nodesMap2.keys.toSet().containsAll(nodesMap1.keys)) {
      // print('Topology comparison: Different node IDs');
      return false;
    }
    
    // Compare each node by ID, name, type, and properties
    for (final id in nodesMap1.keys) {
      final node1 = nodesMap1[id]!;
      final node2 = nodesMap2[id]!;
      
      if (node1.name != node2.name || node1.type != node2.type) {
        // print('Topology comparison: Node $id has different name or type');
        return false;
      }
      
      // Skip comparing status property as it might change frequently
      // but doesn't affect the graph structure
      final properties1 = Map<String, dynamic>.from(node1.properties);
      final properties2 = Map<String, dynamic>.from(node2.properties);
      
      // Remove status property for comparison
      properties1.remove('status');
      properties2.remove('status');
      
      // Compare properties (excluding status)
      if (properties1.length != properties2.length) {
        // print('Topology comparison: Node $id has different property count');
        return false;
      }
      
      for (final key in properties1.keys) {
        if (!properties2.containsKey(key) || properties1[key] != properties2[key]) {
          // print('Topology comparison: Node $id has different property $key');
          return false;
        }
      }
    }
    
    // Create maps of connections by source and target for more efficient comparison
    final connectionsMap1 = <String, NetworkConnection>{};
    final connectionsMap2 = <String, NetworkConnection>{};
    
    for (var conn in connections1) {
      final key = '${conn.sourceId}-${conn.targetId}';
      connectionsMap1[key] = conn;
    }
    
    for (var conn in connections2) {
      final key = '${conn.sourceId}-${conn.targetId}';
      connectionsMap2[key] = conn;
    }
    
    // Check if both topologies have the same connection keys
    if (!connectionsMap1.keys.toSet().containsAll(connectionsMap2.keys) ||
        !connectionsMap2.keys.toSet().containsAll(connectionsMap1.keys)) {
      // print('Topology comparison: Different connection pairs');
      return false;
    }
    
    // Compare each connection by source, target, label, and properties
    for (final key in connectionsMap1.keys) {
      final conn1 = connectionsMap1[key]!;
      final conn2 = connectionsMap2[key]!;
      
      if (conn1.label != conn2.label) {
        // print('Topology comparison: Connection $key has different label');
        return false;
      }
      
      // Compare properties
      if (conn1.properties.length != conn2.properties.length) {
        // print('Topology comparison: Connection $key has different property count');
        return false;
      }
      
      for (final propKey in conn1.properties.keys) {
        if (!conn2.properties.containsKey(propKey) || conn1.properties[propKey] != conn2.properties[propKey]) {
          // print('Topology comparison: Connection $key has different property $propKey');
          return false;
        }
      }
    }
    
    return true;
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
      
      // Check if the topology has actually changed (ignoring status changes)
      final hasChanged = !_hasReceivedTopology || 
                         !_areTopologiesEquivalent(_topology.nodes, _topology.connections, nodes, connections);
      
      if (hasChanged) {
        // print('Topology has changed, updating graph');
        _topology = NetworkTopology(nodes: nodes, connections: connections);
        _hasReceivedTopology = true;
        notifyListeners();
      } else {
        // print('Topology unchanged, skipping update');
        
        // Even though the structure hasn't changed, we might need to update node statuses
        // Create a new topology with the same structure but updated statuses
        final updatedNodes = <NetworkNode>[];
        
        // Create a map of the new nodes by ID for quick lookup
        final newNodesMap = <String, NetworkNode>{};
        for (var node in nodes) {
          newNodesMap[node.id] = node;
        }
        
        // Update each existing node with new status if available
        for (var oldNode in _topology.nodes) {
          if (newNodesMap.containsKey(oldNode.id)) {
            final newNode = newNodesMap[oldNode.id]!;
            // Only update if status has changed
            if (oldNode.properties['status'] != newNode.properties['status']) {
              updatedNodes.add(NetworkNode(
                id: oldNode.id,
                name: oldNode.name,
                type: oldNode.type,
                properties: {
                  ...oldNode.properties,
                  'status': newNode.properties['status'],
                },
              ));
            } else {
              updatedNodes.add(oldNode);
            }
          } else {
            updatedNodes.add(oldNode);
          }
        }
        
        // Check if any statuses have actually changed
        bool statusChanged = false;
        for (int i = 0; i < updatedNodes.length; i++) {
          if (i >= _topology.nodes.length || 
              updatedNodes[i].properties['status'] != _topology.nodes[i].properties['status']) {
            statusChanged = true;
            break;
          }
        }
        
        // Only update the topology if any statuses have changed
        if (statusChanged) {
          // print('Node statuses have changed, updating topology without redrawing graph');
          _topology = NetworkTopology(nodes: updatedNodes, connections: _topology.connections);
          // Don't call notifyListeners() here to avoid triggering a redraw
        }
      }
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
                severity: 'INFO',
                message: logData.toString(),
                source: 'unknown',
              )
        ).toList();
      } else if (logsData != null) {
        // Handle any unexpected format
        print('Unexpected log data format: ${logsData.runtimeType}');
        newLogs.add(LogEntry(
          timestamp: DateTime.now().toIso8601String(),
          severity: 'WARNING',
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
  
  // Toggle performance graph visibility
  void togglePerformanceGraph() {
    _showPerformanceGraph = !_showPerformanceGraph;
    notifyListeners();
  }
  
  // Get node details
  void getNodeDetails(String nodeId) {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_node_details', {'id': nodeId});
    }
  }
  
  // Get topology view
  void getTopologyView(String view) {
    // Store the selected view
    _selectedTopologyView = view;
    
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_topology', {'view': view});
    }
  }
  
  // Add a push notification
  void _addPushNotification(dynamic data) {
    try {
      final notification = PushNotification.fromJson(data);
      if (notification.state == 'input_required') {
        _pushNotifications.add(notification);
        notifyListeners();
      } else if (notification.state == 'incident_update') {
        // Handle incident progress updates (includes new incident creation)
        _handleIncidentProgressUpdate(notification);
      } else {
        // For any other notification type, just notify listeners
        notifyListeners();
      }
    } catch (e) {
      print('Error adding push notification: $e');
    }
  }
  
  // Handle incident progress updates from resolver agent (includes new incident creation)
  void _handleIncidentProgressUpdate(PushNotification notification) {
    try {
      if (notification.inputData == null) {
        print('No input data in incident update notification');
        return;
      }
      
      final inputData = notification.inputData!;
      final incidentData = inputData['incident_data'];
      
      if (incidentData == null) {
        print('No incident_data in notification input_data');
        return;
      }
      
      // Extract the incident ID from the notification
      final incidentId = notification.taskId ?? notification.contextId;
      if (incidentId == null) {
        print('No incident ID found in notification');
        return;
      }
      
      // Extract progress data from the notification with consistent field mapping
      final strategy = inputData['strategy'];
      final rootCause = inputData['root_cause']; // Handle both field names
      final resolution = inputData['resolution'];
      
      print('Socket notification incident update for $incidentId:');
      print('  - Has strategy: ${strategy != null}');
      print('  - Has rootCause: ${rootCause != null}');
      print('  - Has resolution: ${resolution != null}');
      
      // Find the existing incident
      final incidentIndex = _incidents.indexWhere((incident) => 
        incident.id == incidentId || incident.agentTaskId == incidentId);
      
      if (incidentIndex == -1) {
        // Create new incident from notification data
        print('Creating new incident from socket notification: $incidentId');
        
        final incident = incidentData['incident'];
        if (incident == null) {
          print('No incident object in incident_data');
          return;
        }
        
        // Extract timestamp from incident data if available, otherwise use current time
        DateTime recordedTimestamp = DateTime.now();
        if (incident['recordedTimestamp'] != null) {
          try {
            if (incident['recordedTimestamp'] is int) {
              recordedTimestamp = DateTime.fromMillisecondsSinceEpoch(incident['recordedTimestamp']);
            } else if (incident['recordedTimestamp'] is String) {
              recordedTimestamp = DateTime.parse(incident['recordedTimestamp']);
            }
          } catch (e) {
            print('Error parsing recordedTimestamp from socket notification: $e');
          }
        }
        
        // Create new incident from the notification data with consistent field mapping
        final newIncident = Incident(
          id: incidentId,
          recordedTimestamp: recordedTimestamp,
          agentTaskId: incidentId,
          issue: Map<String, dynamic>.from(incident),
          strategy: strategy != null ? Map<String, dynamic>.from(strategy) : null,
          rootCause: rootCause != null ? rootCause.toString() : null,
          resolution: resolution != null ? resolution.toString() : null,
          lastProgressUpdate: DateTime.now(),
        );
        
        _incidents.add(newIncident);
        print('Created new incident $incidentId from socket with progress: strategy=${strategy != null}, rootCause=${rootCause != null}, resolution=${resolution != null}');
        print('  - Progress stage: ${newIncident.progressStage}');
        print('  - Progress percentage: ${newIncident.progressPercentage}');
      } else {
        // Update existing incident with progress information
        final existingIncident = _incidents[incidentIndex];
        final updatedIncident = existingIncident.copyWith(
          strategy: strategy != null ? Map<String, dynamic>.from(strategy) : null,
          rootCause: rootCause != null ? rootCause.toString() : null,
          resolution: resolution != null ? resolution.toString() : null,
          lastProgressUpdate: DateTime.now(),
        );
        
        _incidents[incidentIndex] = updatedIncident;
        print('Updated existing incident $incidentId from socket with progress: strategy=${strategy != null}, rootCause=${rootCause != null}, resolution=${resolution != null}');
        print('  - Progress stage: ${updatedIncident.progressStage}');
        print('  - Progress percentage: ${updatedIncident.progressPercentage}');
      }
      
      // Notify listeners to update the UI
      notifyListeners();
      
    } catch (e) {
      print('Error handling incident progress update: $e');
      // Log the error but don't fall back to REST API - keep the socket-based approach
      print('Notification data: ${notification.toJson()}');
    }
  }
  
  // Fetch incidents from the supervisor REST API
  Future<void> _fetchIncidents() async {
    try {
      print('Fetching running incidents from supervisor REST API...');
      _isLoadingIncidents = true;
      notifyListeners();
      
      // Fetch all open incidents from the API
      final incidents = await _apiService.getAllOpenIncidents();
      
      // Update the incidents list
      _incidents.clear();
      _incidents.addAll(incidents);
      
      print('Successfully fetched ${incidents.length} running incidents');
      _isLoadingIncidents = false;
      notifyListeners();
      
    } catch (e) {
      print('Error fetching incidents: $e');
      _isLoadingIncidents = false;
      notifyListeners();
    }
  }
  
  // Manually refresh incidents
  Future<void> refreshIncidents() async {
    print('Refreshing incidents from supervisor REST API...');
    await _fetchIncidents();
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
