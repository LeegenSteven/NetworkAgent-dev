import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/network_node.dart';
import '../utils/environment_config.dart';
import 'chat_panel.dart';
import 'network_topology.dart';
import 'network_performance.dart';
import 'markdown_drawer.dart';
import 'log_widget.dart';
import 'settings_screen.dart';
import 'notification_screen.dart';

class NetworkDashboard extends StatefulWidget {
  const NetworkDashboard({super.key});

  @override
  State<NetworkDashboard> createState() => _NetworkDashboardState();
}

class _NetworkDashboardState extends State<NetworkDashboard> {
  // Global key for the scaffold to access the drawer
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  
  // Widget display state
  bool _showChat = false; // Chat is hidden by default
  bool _showPerformanceView = false; // Toggle between topology and performance view
  bool _showLogs = false;
  
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
  }
  
  void _toggleLogs() {
    final appState = Provider.of<Appstate>(context, listen: false);
    setState(() {
      _showLogs = !_showLogs;
      appState.toggleLogs(_showLogs);
    });
  }
  
  void _toggleChat() {
    setState(() {
      _showChat = !_showChat;
    });
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
            return Stack(
              alignment: Alignment.center,
              children: [
                IconButton(
                  icon: Icon(
                    Icons.notifications,
                    color: notificationCount > 0 ? Colors.amber : Colors.white,
                  ),
                  onPressed: () {
                    // Navigate to the notification screen
                    Navigator.of(context).push(
                      MaterialPageRoute(builder: (context) => const NotificationScreen()),
                    );
                  },
                  tooltip: 'Notifications',
                ),
                Positioned(
                  top: 5,
                  right: 5,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: notificationCount > 0 ? Colors.red : Colors.transparent,
                      borderRadius: BorderRadius.circular(12),
                      border: notificationCount > 0 ? null : Border.all(color: Colors.white, width: 1),
                    ),
                    constraints: const BoxConstraints(
                      minWidth: 18,
                      minHeight: 18,
                    ),
                    child: Text(
                      notificationCount > 99 ? '99+' : '$notificationCount',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),
              ],
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
              width: MediaQuery.of(context).size.width * _horizontalSplitRatio,
              child: ChatPanel(socket: appState.socket!),
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
                        metrics: appState.metrics,
                        socket: appState.socket!,
                      )
                    : appState.hasReceivedTopology 
                      ? NetworkTopologyWidget(
                          topology: appState.topology, 
                          socket: appState.socket!,
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
                      logs: appState.logs,
                      socket: appState.socket!,
                      isLoading: appState.isLoadingLogs,
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
