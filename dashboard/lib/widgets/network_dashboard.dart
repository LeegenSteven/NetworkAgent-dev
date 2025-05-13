import 'package:flutter/material.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../models/network_node.dart';
import '../models/log_entry.dart';
import '../models/metrics.dart';
import '../utils/environment_config.dart';
import 'chat_panel.dart';
import 'network_topology.dart';
import 'network_performance.dart';
import 'markdown_drawer.dart';
import 'log_widget.dart';

class NetworkDashboard extends StatefulWidget {
  const NetworkDashboard({super.key});

  @override
  State<NetworkDashboard> createState() => _NetworkDashboardState();
}

class _NetworkDashboardState extends State<NetworkDashboard> {
  // Global key for the scaffold to access the drawer
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  
  // Network topology - start with empty topology, will be populated from socket
  NetworkTopology _topology = NetworkTopology.empty();
  
  // Socket.IO client
  late io.Socket socket;
  bool _isConnected = false;
  bool _hasReceivedTopology = false;
  
  // Log widget state
  bool _showLogs = false;
  List<LogEntry> _logs = [];
  bool _isLoadingLogs = false;
  Metrics _metrics = Metrics({});
  bool _isLoadingMetrics = false;

  // Widget display state
  bool _showChat = false; // Chat is hidden by default
  bool _showPerformanceView = false; // Toggle between topology and performance view
  
  // Control the horizontal split view ratio (chat vs. topology+logs)
  double _horizontalSplitRatio = 0.3; // 30% for chat, 70% for network topology + logs
  static const double _minHorizontalSplitRatio = 0.2;
  static const double _maxHorizontalSplitRatio = 0.8;
  
  // Control the vertical split view ratio (topology vs. logs)
  double _verticalSplitRatio = 0.7; // 70% for topology, 30% for logs
  static const double _minVerticalSplitRatio = 0.3;
  static const double _maxVerticalSplitRatio = 0.9;
  
  @override
  void initState() {
    super.initState();
    _connectToServer();
  }
  
  @override
  void dispose() {
    socket.disconnect();
    super.dispose();
  }
  
  void _connectToServer() {
    // Connect to the NetworkAgent socket server
    socket = io.io(EnvironmentConfig.agentUrl, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': true,
    });
    
    socket.onConnect((_) {
      print('Connected to NetworkAgent server');
      setState(() {
        _isConnected = true;
      });
      
      // Request initial topology data when connected
      socket.emit('get_topology', {'view': NetworkTopologyWidget.defaultView});
    });
    
    socket.onDisconnect((_) {
      print('Disconnected from NetworkAgent server');
      setState(() {
        _isConnected = false;
        // Keep the topology when disconnected
      });
    });
    
    // Listen for topology updates
    socket.on('topology_update', (data) {
      if (data != null && data['elements'] != null) {
        print('Received topology update with ${data['elements'].length} elements');
        _updateTopology(data['elements']);
      }
      
      // If logs are enabled and logs data is included, update logs
      if (_showLogs && data != null && data['logs'] != null) {
        _updateLogs(data['logs']);
      }
    });
    
    // Listen for log updates
    socket.on('logs_update', (data) {
      if (data != null) {
        _updateLogs(data);
      }
    });

  // listen for metrics updates
    socket.on('all_last_metrics_update', (data) {
      if (data != null) {
        _updateMetrics(data);        
      }
    });
    
    
    socket.connect();
  }
  
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
      
      setState(() {
        _logs = newLogs;
        _isLoadingLogs = false;
      });
    } catch (e) {
      print('Error updating logs: $e');
      setState(() {
        _isLoadingLogs = false;
      });
    }
  }

   void _toggleLogs() {
    setState(() {
      _showLogs = !_showLogs;
      if (_showLogs) {
        _isLoadingLogs = true;
        // Request logs from server
        socket.emit('get_logs', {'enabled': true});
      } else {
        // Notify server to stop sending logs
        socket.emit('get_logs', {'enabled': false});
      }
    });
  }
  
  // the _updateMetrics function receives its data as structured
  // in networkagent/metrics.py function fetch_all_last_metrics. 
  // Read the data and build a similar data structure here
  void _updateMetrics(dynamic metricsData) {
    try {
      // Use the Metrics class to parse the metrics data
      final metrics = Metrics.fromJson(metricsData);
      
      setState(() {
        _metrics = metrics;
        _isLoadingMetrics = false;
      });
    } catch (e) {
      print('Error updating metrics: $e');
      setState(() {
        _isLoadingMetrics = false;
      });
    }
  }
  
  void _toggleChat() {
    setState(() {
      _showChat = !_showChat;
    });
  }
  
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
      setState(() {
        _topology = NetworkTopology(nodes: nodes, connections: connections);
        _hasReceivedTopology = true;
      });
    } catch (e) {
      print('Error updating topology: $e');
      // If there's an error, create an empty topology to avoid crashes
      setState(() {
        _topology = NetworkTopology.empty();
        _hasReceivedTopology = true;
      });
    }
  }
  
  // Markdown content for the drawer
  String get _markdownContent => '''
# Network Agent Resources

* [GCP project ${EnvironmentConfig.gcpProject}](https://console.cloud.google.com/home/dashboard?project=${EnvironmentConfig.gcpProject})
* [Spanner Graph database](https://console.cloud.google.com/spanner/instances/networktopology-instance/databases/networktopology-db/details/tables?invt=Abiyrw&project=${EnvironmentConfig.gcpProject})
* [Cluster Config status](https://console.cloud.google.com/kubernetes/config_management/packages?project=${EnvironmentConfig.gcpProject})
* [GitOps repository](${EnvironmentConfig.giteaUrl}/networkagent)
* [Demo Scenario](https://docs.google.com/document/d/1gwCnLlgDaRWUv7I_hqd8aRv4B0ICsC7tj3pU7C8MRw0/edit?usp=sharing)
''';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D47A1), // Dark blue
        foregroundColor: Colors.white,
        centerTitle: true, // Center the title
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Network Agent Dashboard',
              style: TextStyle(
                fontWeight: FontWeight.bold, // Make the title bold
              ),
            ),
            const SizedBox(width: 8),
            // Connection status indicator
            Container(
              width: 12,
              height: 12,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: _isConnected ? Colors.green : Colors.red,
              ),
            ),
          ],
        ),
        actions: [
          // Connection status text
          Center(
            child: Padding(
              padding: const EdgeInsets.only(right: 8.0),
              child: Text(
                _isConnected ? 'Connected' : 'Disconnected',
                style: TextStyle(
                  fontSize: 12,
                  color: _isConnected ? Colors.green[100] : Colors.red[100],
                ),
              ),
            ),
          ),
          // View toggle button
          IconButton(
            icon: Icon(
              _showPerformanceView ? Icons.device_hub : Icons.speed,
              color: Colors.white,
            ),
            onPressed: () {
              setState(() {
                _showPerformanceView = !_showPerformanceView;
              });
            },
            tooltip: _showPerformanceView ? 'Show Topology' : 'Show Performance',
          ),
          // Chat toggle button
          IconButton(
            icon: Icon(
              Icons.chat,
              color: _showChat ? Colors.amber : Colors.white,
            ),
            onPressed: () => _toggleChat(),
            tooltip: 'Toggle Chat',
          ),
          // Log toggle button
          IconButton(
            icon: Icon(
              Icons.list_alt,
              color: _showLogs ? Colors.amber : Colors.white,
            ),
            onPressed: _toggleLogs,
            tooltip: 'Toggle Logs',
          ),
          IconButton(
            icon: const Icon(Icons.menu_book),
            onPressed: () {
              // Open the drawer when the documentation icon is pressed
              _scaffoldKey.currentState?.openEndDrawer();
            },
            tooltip: 'Documentation',
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () {
              // In the future, this will open settings
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Settings will be implemented in the future')),
              );
            },
            tooltip: 'Settings',
          ),
        ],
      ),
      // Add the markdown drawer as the end drawer
      endDrawer: MarkdownDrawer(
        markdownContent: _markdownContent,
        title: 'Network Agent Resources',
      ),
      body: Row(
        children: [
          // Left panel - Chat (only shown if _showChat is true)
          if (_showChat) ...[
            SizedBox(
              width: MediaQuery.of(context).size.width * _horizontalSplitRatio,
              child: ChatPanel(socket: socket),
            ),
            
            // Horizontal resizable divider
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _horizontalSplitRatio += details.delta.dx / MediaQuery.of(context).size.width;
                  _horizontalSplitRatio = _horizontalSplitRatio.clamp(_minHorizontalSplitRatio, _maxHorizontalSplitRatio);
                });
              },
              child: Container(
                width: 8,
                color: const Color(0xFFE3F2FD), // Light blue background
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(
                      width: 2,
                      height: 30,
                      color: const Color(0xFF90CAF9), // Slightly darker blue for the handle
                    ),
                  ],
                ),
              ),
            ),
          ],
          
          // Right panel - Network Topology and Logs
          Expanded(
            child: Column(
              children: [
                // Network Topology or Performance Widget
                Expanded(
                  flex: (_verticalSplitRatio * 100).round(), // Convert ratio to flex units
                  child: _showPerformanceView
                    ? NetworkPerformanceWidget(
                        metrics: _metrics,
                        socket: socket,
                      )
                    : _hasReceivedTopology 
                      ? NetworkTopologyWidget(
                          topology: _topology, 
                          socket: socket,
                        )
                      : Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const CircularProgressIndicator(),
                                const SizedBox(height: 16),
                                Text(
                                  'Waiting for network topology data...',
                                  style: TextStyle(
                                    color: Colors.grey[700],
                                    fontStyle: FontStyle.italic,
                                  ),
                                ),
                              ],
                            ),
                          )
                ),
                
                // Only show the vertical divider and logs panel when logs are enabled
                if (_showLogs) ...[
                  // Vertical resizable divider
                  GestureDetector(
                    behavior: HitTestBehavior.translucent,
                    onVerticalDragUpdate: (details) {
                      setState(() {
                        // Calculate the new ratio based on the drag
                        final totalHeight = MediaQuery.of(context).size.height;
                        _verticalSplitRatio += details.delta.dy / totalHeight;
                        _verticalSplitRatio = _verticalSplitRatio.clamp(_minVerticalSplitRatio, _maxVerticalSplitRatio);
                      });
                    },
                    child: Container(
                      height: 8,
                      color: const Color(0xFFE3F2FD), // Light blue background
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Container(
                            width: 30,
                            height: 2,
                            color: const Color(0xFF90CAF9), // Slightly darker blue for the handle
                          ),
                        ],
                      ),
                    ),
                  ),
                  
                  // Logs panel
                  Expanded(
                    flex: ((1 - _verticalSplitRatio) * 100).round(), // Convert ratio to flex units
                    child: LogWidget(
                      logs: _logs,
                      socket: socket,
                      isLoading: _isLoadingLogs,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
