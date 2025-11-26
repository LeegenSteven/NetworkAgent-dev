import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/network_node.dart';
import '../utils/environment_config.dart';
import '../widgets/agui_chat_panel.dart';
import '../widgets/topology/network_topology.dart';
import '../widgets/markdown_drawer.dart';
import '../widgets/log_widget.dart';
import '../widgets/trace/trace_widget.dart';
import '../widgets/performance/performance_graph_widget.dart';
import 'settings_screen.dart';
import 'notification_screen.dart';

class NetworkDashboard extends StatefulWidget {
  const NetworkDashboard({super.key});

  @override
  State<NetworkDashboard> createState() => _NetworkDashboardState();
}

class _NetworkDashboardState extends State<NetworkDashboard> with TickerProviderStateMixin {
  // Global key for the scaffold to access the drawer
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  
  // Widget display state
  bool _showChat = false; // Chat is hidden by default
  bool _showLogs = false;
  bool _showTrace = false;
  
  // Control the horizontal split view ratios
  double _chatPanelRatio = 0.3; // 30% for chat panel
  double _tracePanelRatio = 0.3; // 30% for trace panel
  static const double _minHorizontalSplitRatio = 0.2;
  static const double _maxHorizontalSplitRatio = 0.8;
  
  // Control the vertical split view ratio (topology vs. logs)
  double _verticalSplitRatio = 0.7; // 70% for topology, 30% for logs
  static const double _minVerticalSplitRatio = 0.3;
  static const double _maxVerticalSplitRatio = 0.9;
  
  // Animation controllers for notification vibration
  late AnimationController _vibrationController;
  late Animation<double> _vibrationAnimation;
  late AnimationController _intervalController;
  
  @override
  void initState() {
    super.initState();
    
    // Initialize animation controllers
    _vibrationController = AnimationController(
      duration: const Duration(milliseconds: 600),
      vsync: this,
    );
    
    _vibrationAnimation = Tween<double>(
      begin: 1.0,
      end: 1.3,
    ).animate(CurvedAnimation(
      parent: _vibrationController,
      curve: Curves.easeInOut,
    ));
    
    _intervalController = AnimationController(
      duration: const Duration(seconds: 3),
      vsync: this,
    );
    
    // Start the interval animation that triggers vibration every 3 seconds
    _intervalController.addStatusListener((status) {
      if (status == AnimationStatus.completed) {
        final appState = Provider.of<Appstate>(context, listen: false);
        final hasAnyAlerts = appState.pushNotifications.isNotEmpty || appState.incidents.isNotEmpty;
        
        if (hasAnyAlerts) {
          _triggerVibration();
        }
        
        _intervalController.reset();
        _intervalController.forward();
      }
    });
    
    // Load incidents on startup
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final appState = Provider.of<Appstate>(context, listen: false);
      appState.refreshIncidents();
      
      // Start the interval timer
      _intervalController.forward();
    });
  }
  
  void _triggerVibration() {
    _vibrationController.reset();
    _vibrationController.forward().then((_) {
      _vibrationController.reverse();
    });
  }
  
  @override
  void dispose() {
    _vibrationController.dispose();
    _intervalController.dispose();
    super.dispose();
  }
  
  void _toggleLogs() {
    final appState = Provider.of<Appstate>(context, listen: false);
    setState(() {
      _showLogs = !_showLogs;
      appState.toggleLogs(_showLogs);
      
      // If logs are being shown, hide performance graph
      if (_showLogs && appState.showPerformanceGraph) {
        appState.togglePerformanceGraph();
      }
    });
  }
  
  void _togglePerformanceGraph() {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.togglePerformanceGraph();
    
    // If performance graph is being shown, hide logs
    if (appState.showPerformanceGraph && _showLogs) {
      setState(() {
        _showLogs = false;
        appState.toggleLogs(false);
      });
    }
  }
  
  void _toggleChat() {
    setState(() {
      _showChat = !_showChat;
    });
  }

  void _toggleTrace() {
    final appState = Provider.of<Appstate>(context, listen: false);
    setState(() {
      _showTrace = !_showTrace;
      appState.toggleTraces(_showTrace);
    });
  }
  
  // Markdown content for the drawer
  String get _markdownContent => '''
# Network Agent Resources

* [GCP project ${EnvironmentConfig.gcpProject}](https://console.cloud.google.com/home/dashboard?project=${EnvironmentConfig.gcpProject})
* [Spanner Graph database](https://console.cloud.google.com/spanner/instances/networktopology-instance/databases/networktopology-db/details/tables?invt=Abiyrw&project=${EnvironmentConfig.gcpProject})
* [Cluster Config status](https://console.cloud.google.com/kubernetes/config_management/packages?project=${EnvironmentConfig.gcpProject})
* [GitOps repository](${EnvironmentConfig.giteaUrl}/${EnvironmentConfig.username})
* [Demo Scenario](https://docs.google.com/document/d/1Cq-5Ns4aIPec7MiJSOb4ECnEc0qrDgJfuvoLhsNMoN8/edit?usp=sharing&resourcekey=0-FqKStuCPLuhee9IkbDNFcQ)
''';

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);
    
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D47A1), // Dark blue
        foregroundColor: Colors.white,
        centerTitle: true, // Center the title
        leading: Consumer<Appstate>(
          builder: (context, appState, child) {
            final notificationCount = appState.pushNotifications.length;
            final incidentCount = appState.incidents.length;
            final hasAnyAlerts = notificationCount > 0 || incidentCount > 0;
            
            return AnimatedBuilder(
              animation: _vibrationAnimation,
              builder: (context, child) {
                return Transform.scale(
                  scale: _vibrationAnimation.value,
                  child: IconButton(
                    icon: Icon(
                      Icons.notifications,
                      color: hasAnyAlerts ? Colors.red : Colors.white,
                    ),
                    onPressed: () {
                      // Navigate to the notification screen
                      Navigator.of(context).push(
                        MaterialPageRoute(builder: (context) => const NotificationScreen()),
                      );
                    },
                    tooltip: 'Notifications & Incidents (${notificationCount + incidentCount})',
                  ),
                );
              },
            );
          },
        ),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Google logo
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.asset(
                'assets/images/google.png',
                width: 24,
                height: 24,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(width: 12),
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
                color: appState.isConnected ? Colors.green : Colors.red,
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
                appState.isConnected ? 'Connected' : 'Disconnected',
                style: TextStyle(
                  fontSize: 12,
                  color: appState.isConnected ? Colors.green[100] : Colors.red[100],
                ),
              ),
            ),
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
          // Trace toggle button
          IconButton(
            icon: Icon(
              Icons.analytics,
              color: _showTrace ? Colors.amber : Colors.white,
            ),
            onPressed: () => _toggleTrace(),
            tooltip: 'Toggle Trace',
          ),
          // Performance graph toggle button
          Consumer<Appstate>(
            builder: (context, appState, child) {
              return IconButton(
                icon: Icon(
                  Icons.show_chart,
                  color: appState.showPerformanceGraph ? Colors.amber : Colors.white,
                ),
                onPressed: () => _togglePerformanceGraph(),
                tooltip: 'Toggle Performance Graphs',
              );
            },
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
              // Navigate to the settings screen
              Navigator.of(context).push(
                MaterialPageRoute(builder: (context) => const SettingsScreen()),
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
              width: MediaQuery.of(context).size.width * _chatPanelRatio,
              child: AGUIChatPanel(socket: appState.socket!),
            ),
            
            // Horizontal resizable divider for chat panel
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _chatPanelRatio += details.delta.dx / MediaQuery.of(context).size.width;
                  _chatPanelRatio = _chatPanelRatio.clamp(_minHorizontalSplitRatio, _maxHorizontalSplitRatio);
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
          
          // Main panel - Network Topology and Logs
          Expanded(
            child: Column(
              children: [
                // Main content area - Always show Network Topology
                Expanded(
                  flex: (_verticalSplitRatio * 100).round(), // Convert ratio to flex units
                  child: appState.hasReceivedTopology 
                      ? Consumer<Appstate>(
                          // Listen to topology changes only - filter and layout changes are handled internally
                          builder: (context, appState, child) {
                            // Create a key based only on the topology to avoid excessive rebuilds
                            final topologyKey = ValueKey(
                              'topology-${appState.topology.nodes.length}-'
                              '${appState.topology.connections.length}'
                            );
                            return NetworkTopologyWidget(
                              key: topologyKey,
                              topology: appState.topology,
                            );
                          },
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
                
                // Show the vertical divider and bottom panel when logs or performance graph are enabled
                Consumer<Appstate>(
                  builder: (context, appState, child) {
                    if (_showLogs || appState.showPerformanceGraph) {
                      return Expanded(
                        flex: ((1 - _verticalSplitRatio) * 100).round(), // Convert ratio to flex units
                        child: Column(
                          children: [
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
                            
                            // Bottom panel - show either logs or performance graph
                            Expanded(
                              child: _showLogs 
                                  ? LogWidget(
                                      logs: appState.logs,
                                      socket: appState.socket!,
                                      isLoading: appState.isLoadingLogs,
                                    )
                                  : PerformanceGraphWidget(
                                      socket: appState.socket!,
                                      isLoading: appState.isLoadingMetrics,
                                    ),
                            ),
                          ],
                        ),
                      );
                    } else {
                      return const SizedBox.shrink();
                    }
                  },
                ),
              ],
            ),
          ),
          
          // Right panel - Trace (only shown if _showTrace is true)
          if (_showTrace) ...[
            // Horizontal resizable divider for trace panel
            GestureDetector(
              behavior: HitTestBehavior.translucent,
              onHorizontalDragUpdate: (details) {
                setState(() {
                  _tracePanelRatio -= details.delta.dx / MediaQuery.of(context).size.width;
                  _tracePanelRatio = _tracePanelRatio.clamp(_minHorizontalSplitRatio, _maxHorizontalSplitRatio);
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
            
            SizedBox(
              width: MediaQuery.of(context).size.width * _tracePanelRatio,
              child: const TraceWidget(),
            ),
          ],
        ],
      ),
    );
  }
}
