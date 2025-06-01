import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';
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
          
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: NotificationTable(notifications: notifications),
              ),
            ],
          );
        },
      ),
    );
  }
}

class NotificationTable extends StatefulWidget {
  final List<PushNotification> notifications;

  const NotificationTable({
    super.key,
    required this.notifications,
  });

  @override
  State<NotificationTable> createState() => _NotificationTableState();
}

class _NotificationTableState extends State<NotificationTable> {
  // Set to track which notification IDs are expanded
  final Set<String> _expandedCards = {};

  // Toggle card expansion
  void _toggleCardExpansion(String notificationId) {
    setState(() {
      if (_expandedCards.contains(notificationId)) {
        _expandedCards.remove(notificationId);
      } else {
        _expandedCards.add(notificationId);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: Colors.white,
      child: ListView.builder(
        padding: const EdgeInsets.all(8.0),
        itemCount: widget.notifications.length,
        itemBuilder: (context, index) {
          final notification = widget.notifications[index];
          final isExpanded = _expandedCards.contains(notification.id);
          
          return Padding(
            padding: const EdgeInsets.only(bottom: 8.0),
            child: Card(
              elevation: 2,
              margin: EdgeInsets.zero,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
                side: BorderSide(color: Colors.grey.shade300, width: 1),
              ),
              child: InkWell(
                onTap: () => _toggleCardExpansion(notification.id),
                borderRadius: BorderRadius.circular(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Card header
                    Padding(
                      padding: const EdgeInsets.all(12.0),
                      child: Row(
                        children: [
                          // Name
                          Expanded(
                            child: Text(
                              notification.name,
                              style: TextStyle(
                                fontWeight: notification.isRead ? FontWeight.normal : FontWeight.bold,
                                fontSize: 16,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          
                          // State chip with shadow
                          Container(
                            decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(16),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.2),
                                  blurRadius: 2,
                                  offset: const Offset(0, 1),
                                ),
                              ],
                            ),
                            child: Chip(
                              label: Text(
                                notification.state,
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: Colors.white,
                                ),
                              ),
                              backgroundColor: _getStateColor(notification.state),
                              padding: EdgeInsets.zero,
                              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(16),
                                side: BorderSide.none,
                              ),
                            ),
                          ),
                          
                          const SizedBox(width: 8),
                          
                          // Timestamp
                          Text(
                            _formatTimestamp(notification.timestamp),
                            style: const TextStyle(
                              fontSize: 12,
                              color: Colors.grey,
                            ),
                          ),
                          
                          const SizedBox(width: 8),
                          
                          // Expand/collapse icon
                          Icon(
                            isExpanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                            color: Colors.grey,
                          ),
                        ],
                      ),
                    ),
                    
                    // Content preview (when collapsed)
                    if (!isExpanded)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(12.0, 0.0, 12.0, 12.0),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Divider(),
                            Text(
                              _getFirstLines(notification.content),
                              style: const TextStyle(fontSize: 14),
                              overflow: TextOverflow.ellipsis,
                              maxLines: 1,
                            ),
                            if (_hasMoreContent(notification.content))
                              Padding(
                                padding: const EdgeInsets.only(top: 4.0),
                                child: Text(
                                  'Tap to expand...',
                                  style: TextStyle(
                                    color: Theme.of(context).colorScheme.primary,
                                    fontSize: 12,
                                    fontStyle: FontStyle.italic,
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                    
                    // Expanded content
                    if (isExpanded) ...[
                      const Divider(height: 1),
                      Padding(
                        padding: const EdgeInsets.all(12.0),
                        child: MarkdownBody(
                          data: notification.content,
                          styleSheet: MarkdownStyleSheet(
                            p: const TextStyle(fontSize: 14),
                            h1: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                            h2: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                            h3: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold),
                            code: const TextStyle(
                              backgroundColor: Color(0xFFE1F5FE),
                              color: Color(0xFF01579B),
                              fontSize: 13,
                            ),
                            codeblockDecoration: BoxDecoration(
                              color: const Color(0xFFE1F5FE),
                              borderRadius: BorderRadius.circular(4.0),
                            ),
                            blockquote: const TextStyle(
                              color: Colors.grey,
                              fontStyle: FontStyle.italic,
                              fontSize: 13,
                            ),
                          ),
                          onTapLink: (text, href, title) async {
                            if (href != null) {
                              try {
                                final Uri url = Uri.parse(href);
                                if (await canLaunchUrl(url)) {
                                  await launchUrl(url, mode: LaunchMode.externalApplication);
                                }
                              } catch (e) {
                                // Ignore link errors
                              }
                            }
                          },
                        ),
                      ),
                      
                      // Action buttons
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 8.0),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.end,
                          children: [
                            // Thumbs up
                            TextButton.icon(
                              icon: const Icon(
                                Icons.thumb_up_outlined,
                                color: Colors.green,
                                size: 20,
                              ),
                              label: const Text('Approve'),
                              onPressed: () {
                                // Get app state
                                final appState = Provider.of<Appstate>(context, listen: false);
                                
                                // Send notification feedback to supervisor
                                if (appState.socket != null && appState.socket!.connected) {
                                  appState.socket!.emit('notification_feedback', {
                                    'notification_id': notification.id,
                                    'feedback': 'approve',
                                    'notification_details': notification.toJson(),
                                  });
                                  print('Sent approval feedback to supervisor for notification: ${notification.id}');
                                }
                                
                                // Remove the notification from the list
                                appState.removeNotification(notification.id);
                                
                                // Show confirmation
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                    content: Text('Notification approved'),
                                    backgroundColor: Colors.green,
                                    duration: Duration(seconds: 1),
                                  ),
                                );
                              },
                            ),
                            const SizedBox(width: 8),
                            // Thumbs down
                            TextButton.icon(
                              icon: const Icon(
                                Icons.thumb_down_outlined,
                                color: Colors.red,
                                size: 20,
                              ),
                              label: const Text('Reject'),
                              onPressed: () {
                                // Get app state
                                final appState = Provider.of<Appstate>(context, listen: false);
                                
                                // Send notification feedback to supervisor
                                if (appState.socket != null && appState.socket!.connected) {
                                  appState.socket!.emit('notification_feedback', {
                                    'notification_id': notification.id,
                                    'feedback': 'reject',
                                    'notification_details': notification.toJson(),
                                  });
                                  print('Sent rejection feedback to supervisor for notification: ${notification.id}');
                                }
                                
                                // Remove the notification from the list
                                appState.removeNotification(notification.id);
                                
                                // Show confirmation
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                    content: Text('Notification rejected'),
                                    backgroundColor: Colors.red,
                                    duration: Duration(seconds: 1),
                                  ),
                                );
                              },
                            ),
                          ],
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          );
        },
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
  
  // Helper method to get first line of content
  String _getFirstLines(String content) {
    final lines = content.split('\n');
    if (lines.isEmpty) {
      return '';
    }
    return lines.first;
  }
  
  // Helper method to check if there's more content
  bool _hasMoreContent(String content) {
    return content.split('\n').length > 1;
  }
}
