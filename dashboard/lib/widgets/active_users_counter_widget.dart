import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';

class ActiveUsersCounterWidget extends StatelessWidget {
  const ActiveUsersCounterWidget({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        final activeUsers = _calculateActiveUsers(appState);
        
        return Container(
          padding: const EdgeInsets.all(16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                const Color(0xFF1976D2),
                const Color(0xFF1565C0),
              ],
            ),
            borderRadius: BorderRadius.circular(12.0),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.1),
                blurRadius: 8,
                offset: const Offset(0, 4),
              ),
            ],
            border: Border.all(
              color: const Color(0xFF0D47A1),
              width: 2,
            ),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Legend/Title
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 6.0),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.2),
                  borderRadius: BorderRadius.circular(20.0),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.3),
                    width: 1,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.people,
                      color: Colors.white,
                      size: 16,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      'Active Users',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.5,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              
              // Counter Display
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text(
                    activeUsers.toString(),
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 48,
                      fontWeight: FontWeight.bold,
                      letterSpacing: -1,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'users',
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.8),
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
              
              const SizedBox(height: 12),
              
              // Status indicator
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 4.0),
                decoration: BoxDecoration(
                  color: _getStatusColor(activeUsers).withOpacity(0.2),
                  borderRadius: BorderRadius.circular(12.0),
                  border: Border.all(
                    color: _getStatusColor(activeUsers),
                    width: 1,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        color: _getStatusColor(activeUsers),
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      _getStatusText(activeUsers),
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  int _calculateActiveUsers(Appstate appState) {
    // Use the actual active users count from the new metrics structure
    return appState.metrics.activeUserCount;
  }

  Color _getStatusColor(int userCount) {
    if (userCount < 10) return Colors.orange;
    if (userCount < 50) return Colors.green;
    if (userCount < 100) return Colors.blue;
    return Colors.purple;
  }

  String _getStatusText(int userCount) {
    if (userCount < 10) return 'Low Activity';
    if (userCount < 50) return 'Normal';
    if (userCount < 100) return 'High Activity';
    return 'Peak Usage';
  }
}
