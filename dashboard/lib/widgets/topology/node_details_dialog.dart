import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/network_node.dart';
import '../../utils/APIService.dart';
import '../performance/node_performance.dart';
import '../../screens/notification_screen.dart';

class NodeDetailsDialog extends StatefulWidget {
  final NetworkNode node;

  const NodeDetailsDialog({
    super.key,
    required this.node,
  });

  @override
  State<NodeDetailsDialog> createState() => _NodeDetailsDialogState();
}

class _NodeDetailsDialogState extends State<NodeDetailsDialog> {
  bool _isLoading = true;
  String _markdownSummary = '';
  String? _error;
  final APIService _apiService = APIService();

  @override
  void initState() {
    super.initState();
    _fetchNodeDetails();
  }

  Future<void> _fetchNodeDetails() async {
    try {
      final summary = await _apiService.getNodeDetails(widget.node.id);
      setState(() {
        _isLoading = false;
        _markdownSummary = summary;
      });
    } catch (e) {
      setState(() {
        _isLoading = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
      ),
      elevation: 0,
      backgroundColor: Colors.transparent,
      child: _buildContent(context),
    );
  }

  Widget _buildContent(BuildContext context) {
    // Get the screen size to set a maximum height for the dialog
    final screenSize = MediaQuery.of(context).size;
    final maxHeight = screenSize.height * 0.8; // 80% of screen height
    
    return Container(
      constraints: BoxConstraints(
        maxHeight: maxHeight,
        maxWidth: screenSize.width * 0.9, // 90% of screen width
      ),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        shape: BoxShape.rectangle,
        borderRadius: BorderRadius.circular(16),
        boxShadow: const [
          BoxShadow(
            color: Colors.black26,
            blurRadius: 10.0,
            offset: Offset(0.0, 10.0),
          ),
        ],
      ),
      child: _isLoading
          ? const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(),
                  SizedBox(height: 16),
                  Text('Loading node details...'),
                ],
              ),
            )
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.error_outline, color: Colors.red, size: 48),
                      const SizedBox(height: 16),
                      Text('Error: $_error'),
                      const SizedBox(height: 16),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF0D47A1),
                          foregroundColor: Colors.white,
                        ),
                        onPressed: () {
                          Navigator.of(context).pop();
                        },
                        child: const Text('Close'),
                      ),
                    ],
                  ),
                )
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Header with node type icon and name
                    Row(
                      children: [
                        Icon(
                          widget.node.getIcon(),
                          color: widget.node.getColor(),
                          size: 36,
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                widget.node.name,
                                style: const TextStyle(
                                  fontSize: 20,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              Text(
                                widget.node.type.toString().split('.').last,
                                style: TextStyle(
                                  fontSize: 14,
                                  color: Colors.grey[600],
                                ),
                              ),
                              Row(
                                children: [
                                  SelectableText(
                                    'Id: ${widget.node.id}',
                                    style: TextStyle(
                                      fontSize: 14,
                                      color: Colors.grey[600],
                                    ),
                                  ),
                                  const SizedBox(width: 4),
                                  IconButton(
                                    icon: const Icon(Icons.copy, size: 16),
                                    tooltip: 'Copy ID',
                                    padding: EdgeInsets.zero,
                                    constraints: const BoxConstraints(),
                                    onPressed: () {
                                      Clipboard.setData(ClipboardData(text: widget.node.id));
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        const SnackBar(content: Text('Node ID copied to clipboard')),
                                      );
                                    },
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        // Incident icon for ComputeInstance nodes with incidents
                        Consumer<Appstate>(
                          builder: (context, appState, child) {
                            // Only show for ComputeInstance nodes (NodeType.compute)
                            if (widget.node.type != NodeType.compute) {
                              return const SizedBox.shrink();
                            }
                            
                            // Check if there's an incident for this node
                            final hasIncident = appState.incidents.any((incident) => 
                              incident.title == widget.node.name
                            );
                            
                            if (!hasIncident) {
                              return const SizedBox.shrink();
                            }
                            
                            return IconButton(
                              icon: const Icon(
                                Icons.warning,
                                color: Colors.orange,
                                size: 28,
                              ),
                              tooltip: 'View incidents for this node',
                              onPressed: () {
                                // Close the current dialog first
                                Navigator.of(context).pop();
                                
                                // Navigate to the notification screen
                                Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (context) => const NotificationScreen(),
                                  ),
                                );
                              },
                            );
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    
                    // Scrollable content area
                    // Use Flexible with FlexFit.loose instead of Expanded to allow the column to size itself
                    Flexible(
                      fit: FlexFit.loose,
                      child: SingleChildScrollView(
                        child: Column(
                          mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
                          children: [
                            // Performance Card - Using Consumer to automatically update when metrics change
                            Consumer<Appstate>(
                              builder: (context, appState, child) {
                                final nodeMetrics = appState.metrics.data[widget.node.id];
                                if (nodeMetrics != null && nodeMetrics.isNotEmpty) {
                                  return Container(
                                    margin: const EdgeInsets.symmetric(vertical: 8),
                                    child: StreamBuilder<void>(
                                      // This stream will rebuild whenever appState.metrics changes
                                      stream: Stream.periodic(const Duration(milliseconds: 100))
                                          .asyncMap((_) async => appState.metrics),
                                      builder: (context, snapshot) {
                                        // Get the latest metrics for this node
                                        final latestNodeMetrics = appState.metrics.data[widget.node.id] ?? [];
                                        return NodePerformanceWidget(
                                          metrics: latestNodeMetrics,
                                          nodeId: widget.node.id,
                                          showCpuMetrics: true,
                                          showNetworkMetrics: true,
                                        );
                                      },
                                    ),
                                  );
                                } else {
                                  return const SizedBox.shrink();
                                }
                              },
                            ),
                            // Configuration Card
                            Card(
                              elevation: 2,
                              margin: const EdgeInsets.all(4.0),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(8.0),
                                side: BorderSide(
                                  color: Color(0xFF1976D2),
                                  width: 1.0,
                                ),
                              ),
                              child: Padding(
                                padding: const EdgeInsets.all(8.0),
                                child: Column(
                                  mainAxisSize: MainAxisSize.min, // Allow the column to shrink-wrap its children
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Icon(
                                          Icons.settings,
                                          color: Color(0xFF1976D2),
                                          size: 16,
                                        ),
                                        SizedBox(width: 4),
                                        Text(
                                          'Configuration',
                                          style: TextStyle(
                                            fontSize: 14,
                                            fontWeight: FontWeight.bold,
                                            color: Color(0xFF0D47A1),
                                          ),
                                        ),
                                      ],
                                    ),
                                    Divider(height: 12),
                                    MarkdownBody(
                                      data: _markdownSummary,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    
                    const SizedBox(height: 16),
                    
                    // Actions
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        
                        const SizedBox(width: 8),
                        ElevatedButton(
                          style: ElevatedButton.styleFrom(
                            backgroundColor: const Color(0xFF0D47A1),
                            foregroundColor: Colors.white,
                          ),
                          onPressed: () {
                            Navigator.of(context).pop();
                          },
                          child: const Text('Close'),
                        ),
                      ],
                    ),
                  ],
                ),
    );
  }

}
