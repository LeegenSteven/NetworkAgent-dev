import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:fl_chart/fl_chart.dart';
import '../appstate.dart';
import '../models/metric_entry.dart';
import '../models/network_node.dart';

class NodePerformanceWidget extends StatefulWidget {
  const NodePerformanceWidget({super.key});

  @override
  State<NodePerformanceWidget> createState() => _NodePerformanceWidgetState();
}

class _NodePerformanceWidgetState extends State<NodePerformanceWidget> {
  // Store the last 20 average network traffic values for each node
  final Map<String, List<double>> _nodeTrafficHistory = {};
  final int _maxHistoryLength = 20;
  
  // Colors for different nodes
  final List<Color> _nodeColors = [
    Colors.blue,
    Colors.red,
    Colors.green,
    Colors.orange,
    Colors.purple,
    Colors.teal,
    Colors.pink,
    Colors.indigo,
    Colors.amber,
    Colors.cyan,
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(8.0),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12.0),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.1),
            blurRadius: 8,
            offset: const Offset(0, 4),
          ),
        ],
        border: Border.all(
          color: Colors.grey[300]!,
          width: 1,
        ),
      ),
      child: Column(
        children: [
          Container(
            width: double.infinity,
            height: 40,
            padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
            decoration: const BoxDecoration(
              color: Color(0xFFE3F2FD), // Light blue background
              borderRadius: BorderRadius.only(
                topLeft: Radius.circular(12.0),
                topRight: Radius.circular(12.0),
              ),
            ),
            child: Stack(
              alignment: Alignment.center,
              children: [
                // Centered Title
                Center(
                  child: Consumer<Appstate>(
                    builder: (context, appState, child) {
                      final computeNodes = _getComputeInstanceNodes(appState);
                      return Text(
                        'Network Traffic History (${computeNodes.length} compute instances)',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: Color(0xFF0D47A1), // Dark blue text
                        ),
                      );
                    },
                  ),
                ),
                
                // Clear history button (positioned on the right)
                Positioned(
                  right: 0,
                  child: IconButton(
                    icon: const Icon(Icons.clear_all, color: Color(0xFF0D47A1)),
                    tooltip: 'Clear history',
                    onPressed: () {
                      setState(() {
                        _nodeTrafficHistory.clear();
                      });
                    },
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: Consumer<Appstate>(
              builder: (context, appState, child) {
                _updateTrafficHistory(appState);
                return _buildNetworkTrafficGraph(context, appState);
              },
            ),
          ),
        ],
      ),
    );
  }

  List<NetworkNode> _getComputeInstanceNodes(Appstate appState) {
    return appState.topology.nodes
        .where((node) => node.type == NodeType.compute)
        .toList();
  }

  void _updateTrafficHistory(Appstate appState) {
    final metricsData = appState.metrics.data;
    final computeNodes = _getComputeInstanceNodes(appState);
    
    // Update history for each compute instance node
    for (final node in computeNodes) {
      final nodeMetrics = metricsData[node.id];
      if (nodeMetrics != null && nodeMetrics.isNotEmpty) {
        // Get the latest metric entry
        final latestMetric = nodeMetrics.last;
        
        // Calculate average network traffic (receive + send) in KB/s
        double totalTraffic = 0.0;
        int interfaceCount = 0;
        
        latestMetric.interfaces.forEach((interfaceName, interfaceData) {
          if (interfaceData is Map) {
            final receiveThroughput = (interfaceData['byte_recv_throughput'] as num?)?.toDouble() ?? 0.0;
            final sendThroughput = (interfaceData['byte_sent_throughput'] as num?)?.toDouble() ?? 0.0;
            totalTraffic += (receiveThroughput + sendThroughput) / 1024.0; // Convert to KB/s
            interfaceCount++;
          }
        });
        
        // Calculate average traffic across all interfaces
        final averageTraffic = interfaceCount > 0 ? totalTraffic / interfaceCount : 0.0;
        
        // Initialize history for new nodes
        if (!_nodeTrafficHistory.containsKey(node.id)) {
          _nodeTrafficHistory[node.id] = [];
        }
        
        // Add new data point and maintain max history length
        final history = _nodeTrafficHistory[node.id]!;
        history.add(averageTraffic);
        
        // Keep only the last 20 entries
        if (history.length > _maxHistoryLength) {
          history.removeAt(0);
        }
      }
    }
  }

  Widget _buildNetworkTrafficGraph(BuildContext context, Appstate appState) {
    final computeNodes = _getComputeInstanceNodes(appState);
    
    if (computeNodes.isEmpty) {
      return Center(
        child: Text(
          'No compute instance nodes found in topology',
          style: TextStyle(
            color: Colors.grey[700],
            fontStyle: FontStyle.italic,
          ),
        ),
      );
    }

    if (_nodeTrafficHistory.isEmpty) {
      return Center(
        child: Text(
          'Waiting for network traffic data...',
          style: TextStyle(
            color: Colors.grey[700],
            fontStyle: FontStyle.italic,
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
            'Average Network Traffic Over Time (KB/s)',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
              fontWeight: FontWeight.bold,
              color: const Color(0xFF0D47A1),
            ),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: _buildLineChart(computeNodes),
          ),
          const SizedBox(height: 16),
          _buildLegend(computeNodes),
        ],
      ),
    );
  }

  Widget _buildLineChart(List<NetworkNode> computeNodes) {
    final lineBarsData = <LineChartBarData>[];
    double maxTraffic = 0.0;
    int maxHistoryLength = 0;

    // Create line data for each compute instance node
    for (int i = 0; i < computeNodes.length; i++) {
      final node = computeNodes[i];
      final history = _nodeTrafficHistory[node.id] ?? [];
      
      if (history.isNotEmpty) {
        final spots = <FlSpot>[];
        for (int j = 0; j < history.length; j++) {
          spots.add(FlSpot(j.toDouble(), history[j]));
          if (history[j] > maxTraffic) maxTraffic = history[j];
        }
        
        if (history.length > maxHistoryLength) {
          maxHistoryLength = history.length;
        }

        final color = _nodeColors[i % _nodeColors.length];
        
        lineBarsData.add(
          LineChartBarData(
            spots: spots,
            isCurved: true,
            color: color,
            barWidth: 2,
            isStrokeCapRound: true,
            dotData: FlDotData(
              show: true,
              getDotPainter: (spot, percent, barData, index) {
                return FlDotCirclePainter(
                  radius: 2,
                  color: color,
                  strokeWidth: 1,
                  strokeColor: Colors.white,
                );
              },
            ),
            belowBarData: BarAreaData(show: false),
          ),
        );
      }
    }

    // Handle empty data case
    if (lineBarsData.isEmpty) {
      return Container(
        decoration: BoxDecoration(
          border: Border.all(color: Colors.grey[300]!, width: 1),
        ),
        child: Center(
          child: Text(
            'No data available yet\nWaiting for metrics...',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: Colors.grey[600],
              fontStyle: FontStyle.italic,
            ),
          ),
        ),
      );
    }

    final maxY = maxTraffic > 0 ? (maxTraffic * 1.2) : 100.0;
    final maxX = maxHistoryLength > 0 ? (maxHistoryLength - 1).toDouble() : 19.0;

    return LineChart(
      LineChartData(
        gridData: FlGridData(
          show: true,
          drawVerticalLine: true,
          horizontalInterval: maxY / 5,
          verticalInterval: maxX > 0 ? maxX / 10 : 2,
          getDrawingHorizontalLine: (value) {
            return FlLine(
              color: Colors.grey[300]!,
              strokeWidth: 1,
            );
          },
          getDrawingVerticalLine: (value) {
            return FlLine(
              color: Colors.grey[300]!,
              strokeWidth: 1,
            );
          },
        ),
        titlesData: FlTitlesData(
          show: true,
          rightTitles: AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
          topTitles: AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 30,
              interval: 2,
              getTitlesWidget: (value, meta) {
                return SideTitleWidget(
                  axisSide: meta.axisSide,
                  child: Text(
                    'T-${(_maxHistoryLength - value.toInt() - 1).toString()}',
                    style: const TextStyle(
                      color: Colors.grey,
                      fontWeight: FontWeight.bold,
                      fontSize: 10,
                    ),
                  ),
                );
              },
            ),
          ),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              interval: maxY > 0 ? maxY / 5 : 20,
              reservedSize: 60,
              getTitlesWidget: (value, meta) {
                return SideTitleWidget(
                  axisSide: meta.axisSide,
                  child: Text(
                    '${value.toInt()} KB/s',
                    style: const TextStyle(
                      color: Colors.grey,
                      fontWeight: FontWeight.bold,
                      fontSize: 10,
                    ),
                  ),
                );
              },
            ),
          ),
        ),
        borderData: FlBorderData(
          show: true,
          border: Border.all(color: Colors.grey[300]!, width: 1),
        ),
        minX: 0,
        maxX: maxX,
        minY: 0,
        maxY: maxY,
        lineBarsData: lineBarsData,
      ),
    );
  }

  Widget _buildLegend(List<NetworkNode> computeNodes) {
    return Wrap(
      spacing: 16,
      runSpacing: 8,
      children: computeNodes.asMap().entries.map((entry) {
        final index = entry.key;
        final node = entry.value;
        final color = _nodeColors[index % _nodeColors.length];
        final hostname = node.name.isNotEmpty ? node.name : node.id.substring(0, 8);
        
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 16,
              height: 3,
              decoration: BoxDecoration(
                color: color,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 6),
            Text(
              hostname,
              style: const TextStyle(
                fontSize: 12,
                color: Colors.grey,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        );
      }).toList(),
    );
  }
}
