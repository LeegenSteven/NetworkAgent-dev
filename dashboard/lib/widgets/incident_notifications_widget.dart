import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../appstate.dart';
import '../models/incident.dart';

class IncidentNotificationsWidget extends StatefulWidget {
  const IncidentNotificationsWidget({super.key});

  @override
  State<IncidentNotificationsWidget> createState() => _IncidentNotificationsWidgetState();
}

class _IncidentNotificationsWidgetState extends State<IncidentNotificationsWidget> {
  // Set to track which incident IDs are expanded
  final Set<String> _expandedCards = {};

  @override
  void initState() {
    super.initState();
    // Incidents are already loaded on startup and updated via notifications
    // No need to call refreshIncidents() here
  }

  // Toggle card expansion
  void _toggleCardExpansion(String incidentId) {
    setState(() {
      if (_expandedCards.contains(incidentId)) {
        _expandedCards.remove(incidentId);
      } else {
        _expandedCards.add(incidentId);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        if (appState.isLoadingIncidents) {
          return const Center(
            child: CircularProgressIndicator(),
          );
        }

        final incidents = appState.incidents;
        
        if (incidents.isEmpty) {
          return Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  'No open incidents',
                  style: TextStyle(
                    fontSize: 18,
                    fontStyle: FontStyle.italic,
                    color: Colors.grey,
                  ),
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: () => appState.refreshIncidents(),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Refresh'),
                ),
              ],
            ),
          );
        }
        
        return Column(
          children: [
            // Header with refresh button
            Padding(
              padding: const EdgeInsets.all(8.0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    '${incidents.length} Open Incidents',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  IconButton(
                    onPressed: () => appState.refreshIncidents(),
                    icon: const Icon(Icons.refresh),
                    tooltip: 'Refresh Incidents',
                  ),
                ],
              ),
            ),
            // Incidents list
            Expanded(
              child: Container(
                width: double.infinity,
                color: Colors.white,
                child: ListView.builder(
                  padding: const EdgeInsets.all(8.0),
                  itemCount: incidents.length,
                  itemBuilder: (context, index) {
                    final incident = incidents[index];
                    final isExpanded = _expandedCards.contains(incident.id);
                    
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 8.0),
                      child: Card(
                        elevation: 2,
                        margin: EdgeInsets.zero,
                        color: Colors.white,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                          side: BorderSide(color: Colors.grey.shade300, width: 1),
                        ),
                        child: InkWell(
                          onTap: () => _toggleCardExpansion(incident.id),
                          hoverColor: Colors.transparent,
                          splashColor: Colors.transparent,
                          highlightColor: Colors.transparent,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              // Card header
                              Padding(
                                padding: const EdgeInsets.all(12.0),
                                child: Row(
                                  children: [
                                    const SizedBox(width: 12),
                                    
                                    // Title and details
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            incident.title,
                                            style: const TextStyle(
                                              fontWeight: FontWeight.bold,
                                              fontSize: 16,
                                            ),
                                            overflow: TextOverflow.ellipsis,
                                          ),
                                          const SizedBox(height: 4),
                                          Row(
                                            children: [
                                              if (incident.affectedNode != null) ...[
                                                Icon(
                                                  Icons.device_hub,
                                                  size: 14,
                                                  color: Colors.grey[600],
                                                ),
                                                const SizedBox(width: 4),
                                                Text(
                                                  incident.affectedNode!,
                                                  style: TextStyle(
                                                    fontSize: 12,
                                                    color: Colors.grey[600],
                                                  ),
                                                ),
                                                const SizedBox(width: 12),
                                              ],
                                              if (incident.assignedAgent != null) ...[
                                                Icon(
                                                  Icons.person,
                                                  size: 14,
                                                  color: Colors.grey[600],
                                                ),
                                                const SizedBox(width: 4),
                                                Text(
                                                  incident.assignedAgent!,
                                                  style: TextStyle(
                                                    fontSize: 12,
                                                    color: Colors.grey[600],
                                                  ),
                                                ),
                                              ],
                                            ],
                                          ),
                                        ],
                                      ),
                                    ),
                                    
                                    // State chip
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
                                          incident.state.toUpperCase(),
                                          style: const TextStyle(
                                            fontSize: 10,
                                            color: Colors.white,
                                            fontWeight: FontWeight.bold,
                                          ),
                                        ),
                                        backgroundColor: _getStateColor(incident.state),
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
                                    Column(
                                      crossAxisAlignment: CrossAxisAlignment.end,
                                      children: [
                                        Text(
                                          '${_formatTimestamp(incident.createdAt)} UTC',
                                          style: const TextStyle(
                                            fontSize: 12,
                                            color: Colors.grey,
                                          ),
                                        ),
                                        if (incident.updatedAt != incident.createdAt)
                                          Text(
                                            'Updated ${_formatTimestamp(incident.updatedAt)} UTC',
                                            style: const TextStyle(
                                              fontSize: 10,
                                              color: Colors.grey,
                                            ),
                                          ),
                                      ],
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
                                        _getFirstLines(incident.description),
                                        style: const TextStyle(fontSize: 14),
                                        overflow: TextOverflow.ellipsis,
                                        maxLines: 2,
                                      ),
                                      if (_hasMoreContent(incident.description))
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
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      // Description
                                      const Text(
                                        'Description:',
                                        style: TextStyle(
                                          fontWeight: FontWeight.bold,
                                          fontSize: 14,
                                        ),
                                      ),
                                      const SizedBox(height: 8),
                                      MarkdownBody(
                                        data: incident.description,
                                        styleSheet: MarkdownStyleSheet(
                                          p: const TextStyle(fontSize: 14),
                                          code: const TextStyle(
                                            backgroundColor: Colors.grey,
                                            color: Colors.black,
                                            fontSize: 13,
                                          ),
                                          codeblockDecoration: BoxDecoration(
                                            color: Colors.grey[100],
                                            borderRadius: BorderRadius.circular(4.0),
                                          ),
                                        ),
                                      ),
                                      
                      // Additional incident details
                      const SizedBox(height: 16),
                      const Text(
                        'Incident Details:',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Container(
                        padding: const EdgeInsets.all(12.0),
                        decoration: BoxDecoration(
                          color: Colors.grey[50],
                          borderRadius: BorderRadius.circular(8.0),
                          border: Border.all(color: Colors.grey[300]!),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _buildDetailRow('Agent Task ID', incident.agentTaskId),
                            _buildDetailRow('Recorded', '${_formatTimestamp(incident.recordedTimestamp)} UTC'),
                            if (incident.resolvedTimestamp != null)
                              _buildDetailRow('Resolved', '${_formatTimestamp(incident.resolvedTimestamp!)} UTC'),
                            if (incident.cause != null && incident.cause!.isNotEmpty)
                              _buildDetailRow('Cause', incident.cause.toString()),
                            if (incident.resolution != null && incident.resolution!.isNotEmpty)
                              _buildDetailRow('Resolution', incident.resolution.toString()),
                          ],
                        ),
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
              ),
            ),
          ],
        );
      },
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

  // Helper method to get color based on incident state
  Color _getStateColor(String state) {
    switch (state.toLowerCase()) {
      case 'open':
        return Colors.red;
      case 'investigating':
        return Colors.orange;
      case 'resolved':
        return Colors.green;
      case 'closed':
        return Colors.grey;
      default:
        return Colors.blue;
    }
  }

  
  // Helper method to get first lines of content
  String _getFirstLines(String content) {
    final lines = content.split('\n');
    if (lines.isEmpty) {
      return '';
    }
    return lines.take(2).join('\n');
  }
  
  // Helper method to check if there's more content
  bool _hasMoreContent(String content) {
    return content.split('\n').length > 2;
  }
  
  // Helper method to build detail rows
  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$label: ',
            style: const TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 13,
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
