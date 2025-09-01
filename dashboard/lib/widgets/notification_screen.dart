import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import 'agent_request_notifications_widget.dart';
import 'incident_notifications_widget.dart';

class NotificationScreen extends StatefulWidget {
  const NotificationScreen({super.key});

  @override
  State<NotificationScreen> createState() => _NotificationScreenState();
}

class _NotificationScreenState extends State<NotificationScreen> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white, // Ensure consistent white background
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D47A1), // Dark blue
        foregroundColor: Colors.white,
        title: Center(
          child: Row(
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
                'Notifications',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),
        actions: [
          // Clear all notifications button (only for agent requests tab)
          Consumer<Appstate>(
            builder: (context, appState, child) {
              return IconButton(
                icon: const Icon(Icons.clear_all),
                onPressed: _tabController.index == 0 ? () {
                  appState.clearAllNotifications();
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('All agent requests cleared')),
                  );
                } : null,
                tooltip: _tabController.index == 0 ? 'Clear All Agent Requests' : null,
              );
            },
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: Colors.white,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white70,
          tabs: [
            Consumer<Appstate>(
              builder: (context, appState, child) {
                final requestCount = appState.pushNotifications.length;
                return Tab(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.notifications_active),
                      const SizedBox(width: 8),
                      const Text('Agent Requests'),
                      if (requestCount > 0) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: Colors.red,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            requestCount.toString(),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                );
              },
            ),
            Consumer<Appstate>(
              builder: (context, appState, child) {
                final incidentCount = appState.incidents.length;
                return Tab(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.warning),
                      const SizedBox(width: 8),
                      const Text('Incidents'),
                      if (incidentCount > 0) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: Colors.orange,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            incidentCount.toString(),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                );
              },
            ),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: const [
          AgentRequestNotificationsWidget(),
          IncidentNotificationsWidget(),
        ],
      ),
    );
  }
}
