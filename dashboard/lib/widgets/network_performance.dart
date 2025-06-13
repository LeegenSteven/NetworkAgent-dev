import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../appstate.dart';
import '../models/metrics.dart';
import '../models/metric_entry.dart';
import 'node_performance.dart';

class NetworkPerformanceWidget extends StatefulWidget {
  final Metrics metrics;

  const NetworkPerformanceWidget({
    super.key,
    required this.metrics,
  });

  @override
  State<NetworkPerformanceWidget> createState() => _NetworkPerformanceWidgetState();
}

class _NetworkPerformanceWidgetState extends State<NetworkPerformanceWidget> {
  // Filter options
  bool _showCpuMetrics = true;
  bool _showNetworkMetrics = true;
  
  @override
  void initState() {
    super.initState();
  }
  
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Header with title and filter options
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: LayoutBuilder(
            builder: (context, constraints) {
              final controlsWidth = 200; // Approximate width of all controls
              final hasEnoughSpace = constraints.maxWidth > (controlsWidth * 1.5);
              
              return Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  // Title - either centered in its own space or aligned to the left
                  if (hasEnoughSpace)
                    Expanded(
                      child: Center(
                        child: Text(
                          'Network Performance',
                          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.bold,
                            color: Color(0xFF0D47A1), // Dark blue text
                          ),
                        ),
                      ),
                    )
                  else
                    Padding(
                      padding: const EdgeInsets.only(right: 16.0),
                      child: Text(
                        'Network Performance',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: Color(0xFF0D47A1), // Dark blue text
                        ),
                      ),
                    ),
                  
                  // Controls row
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // CPU metrics toggle
                      Row(
                        children: [
                          Text(
                            'CPU: ',
                            style: TextStyle(
                              color: Color(0xFF0D47A1),
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          Switch(
                            value: _showCpuMetrics,
                            activeColor: Color(0xFF1976D2),
                            onChanged: (value) {
                              setState(() {
                                _showCpuMetrics = value;
                              });
                            },
                          ),
                        ],
                      ),
                      
                      // Network metrics toggle
                      Row(
                        children: [
                          Text(
                            'Network: ',
                            style: TextStyle(
                              color: Color(0xFF0D47A1),
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          Switch(
                            value: _showNetworkMetrics,
                            activeColor: Color(0xFF1976D2),
                            onChanged: (value) {
                              setState(() {
                                _showNetworkMetrics = value;
                              });
                            },
                          ),
                        ],
                      ),
                      
                      // Delete metrics button
                      IconButton(
                        icon: Icon(
                          Icons.delete_outline,
                          color: Color(0xFF0D47A1),
                        ),
                        tooltip: 'Clear all metrics',
                        onPressed: () {
                          // Show confirmation dialog
                          showDialog(
                            context: context,
                            builder: (BuildContext context) {
                              return AlertDialog(
                                title: Text('Clear Metrics'),
                                content: Text('Are you sure you want to clear all performance metrics?'),
                                actions: [
                                  TextButton(
                                    child: Text('Cancel'),
                                    onPressed: () {
                                      Navigator.of(context).pop();
                                    },
                                  ),
                                  TextButton(
                                    child: Text('Clear'),
                                    onPressed: () {
                                      // Use appstate to reset metrics
                                      final appState = Provider.of<Appstate>(context, listen: false);
                                      appState.resetMetrics();
                                      Navigator.of(context).pop();
                                      
                                      // Show snackbar to confirm action
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        SnackBar(
                                          content: Text('Performance metrics cleared'),
                                          duration: Duration(seconds: 2),
                                        ),
                                      );
                                    },
                                  ),
                                ],
                              );
                            },
                          );
                        },
                      ),
                    ],
                  ),
                ],
              );
            },
          ),
        ),
        
        // Main content area
        Expanded(
          child: widget.metrics.data.isEmpty
              ? Center(
                  child: Text(
                    'No performance metrics available',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                )
              : _buildPerformanceContent(),
        ),
      ],
    );
  }

  Widget _buildPerformanceContent() {
    // If there are no nodes with metrics, show a message
    if (widget.metrics.data.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.speed,
              size: 64,
              color: Color(0xFF1976D2),
            ),
            SizedBox(height: 16),
            Text(
              'No Network Performance Data',
              style: TextStyle(
                fontSize: 24,
                fontWeight: FontWeight.bold,
                color: Color(0xFF0D47A1),
              ),
            ),
            SizedBox(height: 8),
            Text(
              'Waiting for metrics data from network nodes',
              style: TextStyle(
                fontSize: 16,
                color: Colors.grey[700],
              ),
            ),
          ],
        ),
      );
    }
    
    return Column(
      children: [
        // Header with count
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8.0),
          child: Text(
            '(${widget.metrics.data.length} Network Nodes)',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.bold,
              color: Color(0xFF0D47A1),
            ),
            textAlign: TextAlign.center,
          ),
        ),
        
        // Grid of node performance widgets
        Expanded(
          child: LayoutBuilder(
            builder: (context, constraints) {
              // Calculate how many cards can fit in a row based on available width
              // Assuming each card needs at least 200px width for readability
              final double cardWidth = 200;
              final int crossAxisCount = (constraints.maxWidth / cardWidth).floor();
              
              return GridView.builder(
                padding: const EdgeInsets.all(8.0),
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: crossAxisCount > 0 ? crossAxisCount : 1,
                  childAspectRatio: 0.7, // Further reduced to prevent overflow
                  crossAxisSpacing: 8.0,
                  mainAxisSpacing: 8.0,
                ),
                itemCount: widget.metrics.data.length,
                itemBuilder: (context, index) {
                  final entry = widget.metrics.data.entries.elementAt(index);
                  final nodeId = entry.key;
                  final nodeMetrics = entry.value;
                  
                  return NodePerformanceWidget(
                    nodeId: nodeId,
                    metrics: nodeMetrics,
                    showCpuMetrics: _showCpuMetrics,
                    showNetworkMetrics: _showNetworkMetrics,
                  );
                },
              );
            },
          ),
        ),
      ],
    );
  }
}
