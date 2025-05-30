import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/push_notification.dart';

class NotificationScreen extends StatelessWidget {
  const NotificationScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
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
                'Agent Notifications',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),
        actions: [
          // Clear all notifications button
          IconButton(
            icon: const Icon(Icons.clear_all),
            onPressed: () {
              final appState = Provider.of<Appstate>(context, listen: false);
              appState.clearAllNotifications();
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('All notifications cleared')),
              );
            },
            tooltip: 'Clear All Notifications',
          ),
        ],
      ),
      body: Consumer<Appstate>(
        builder: (context, appState, child) {
          final notifications = appState.pushNotifications;
          
          if (notifications.isEmpty) {
            return const Center(
              child: Text(
                'No notifications',
                style: TextStyle(
                  fontSize: 18,
                  fontStyle: FontStyle.italic,
                  color: Colors.grey,
                ),
              ),
            );
          }
          
          return Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'You have ${notifications.length} notification${notifications.length != 1 ? 's' : ''}',
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 16),
                Expanded(
                  child: Card(
                    elevation: 4,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(12),
                      child: NotificationTable(notifications: notifications),
                    ),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class NotificationTable extends StatelessWidget {
  final List<PushNotification> notifications;

  const NotificationTable({
    super.key,
    required this.notifications,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.vertical,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: DataTable(
          headingRowColor: MaterialStateProperty.all(
            const Color(0xFFE3F2FD), // Light blue background
          ),
          dataRowMinHeight: 64,
          dataRowMaxHeight: 80,
          columns: const [
            DataColumn(
              label: Text(
                'Status',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            DataColumn(
              label: Text(
                'Name',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            DataColumn(
              label: Text(
                'State',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            DataColumn(
              label: Text(
                'Content',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            DataColumn(
              label: Text(
                'Time',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            DataColumn(
              label: Text(
                'Actions',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
          ],
          rows: notifications.map((notification) {
            return DataRow(
              color: notification.isRead
                  ? null
                  : MaterialStateProperty.all(const Color(0xFFF5F5F5)), // Light grey for unread
              cells: [
                // Status (read/unread)
                DataCell(
                  Icon(
                    notification.isRead ? Icons.mark_email_read : Icons.mark_email_unread,
                    color: notification.isRead ? Colors.grey : Colors.blue,
                  ),
                ),
                // Name
                DataCell(
                  Text(
                    notification.name,
                    style: TextStyle(
                      fontWeight: notification.isRead ? FontWeight.normal : FontWeight.bold,
                    ),
                  ),
                ),
                // State
                DataCell(
                  Chip(
                    label: Text(
                      notification.state,
                      style: const TextStyle(
                        fontSize: 12,
                        color: Colors.white,
                      ),
                    ),
                    backgroundColor: _getStateColor(notification.state),
                    padding: EdgeInsets.zero,
                  ),
                ),
                // Content
                DataCell(
                  SizedBox(
                    width: 300, // Limit width to prevent very wide tables
                    child: Text(
                      notification.content,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ),
                // Timestamp
                DataCell(
                  Text(
                    _formatTimestamp(notification.timestamp),
                    style: const TextStyle(
                      fontSize: 12,
                      color: Colors.grey,
                    ),
                  ),
                ),
                // Actions
                DataCell(
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // Mark as read/unread
                      IconButton(
                        icon: Icon(
                          notification.isRead ? Icons.visibility_off : Icons.visibility,
                          color: Colors.blue,
                          size: 20,
                        ),
                        onPressed: () {
                          final appState = Provider.of<Appstate>(context, listen: false);
                          appState.markNotificationAsRead(notification.id);
                        },
                        tooltip: notification.isRead ? 'Mark as unread' : 'Mark as read',
                      ),
                      // View details (if applicable)
                      if (notification.taskId != null || notification.contextId != null)
                        IconButton(
                          icon: const Icon(
                            Icons.open_in_new,
                            color: Colors.green,
                            size: 20,
                          ),
                          onPressed: () {
                            // TODO: Implement navigation to task/context details
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                content: Text(
                                  'View details for ${notification.taskId ?? notification.contextId}',
                                ),
                              ),
                            );
                          },
                          tooltip: 'View details',
                        ),
                    ],
                  ),
                ),
              ],
            );
          }).toList(),
        ),
      ),
    );
  }

  // Helper method to format timestamp
  String _formatTimestamp(DateTime timestamp) {
    final now = DateTime.now();
    final difference = now.difference(timestamp);
    
    if (difference.inDays > 0) {
      return '${difference.inDays}d ago';
    } else if (difference.inHours > 0) {
      return '${difference.inHours}h ago';
    } else if (difference.inMinutes > 0) {
      return '${difference.inMinutes}m ago';
    } else {
      return 'Just now';
    }
  }

  // Helper method to get color based on notification state
  Color _getStateColor(String state) {
    switch (state.toLowerCase()) {
      case 'input_required':
        return Colors.orange;
      case 'completed':
        return Colors.green;
      case 'error':
        return Colors.red;
      case 'warning':
        return Colors.amber;
      case 'info':
        return Colors.blue;
      default:
        return Colors.grey;
    }
  }
}
