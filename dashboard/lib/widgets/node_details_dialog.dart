import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../appstate.dart';
import '../models/network_node.dart';

class NodeDetailsDialog extends StatefulWidget {
  final NetworkNode node;
  final io.Socket socket;

  const NodeDetailsDialog({
    super.key,
    required this.node,
    required this.socket,
  });

  @override
  State<NodeDetailsDialog> createState() => _NodeDetailsDialogState();
}

class _NodeDetailsDialogState extends State<NodeDetailsDialog> {
  bool _isLoading = true;
  Map<String, dynamic> _detailedProperties = {};
  String? _error;

  @override
  void initState() {
    super.initState();
    // Listen for node details response
    widget.socket.on('node_details_response', (data) {
      setState(() {
        _isLoading = false;
        if (data['error'] != null) {
          _error = data['error'];
        } else if (data['id'] == widget.node.id) {
          // Only update if the response is for the current node
          _detailedProperties = data['properties'] ?? {};
        }
      });
    });
  }

  @override
  void dispose() {
    // Remove the listener when the dialog is closed
    widget.socket.off('node_details_response');
    super.dispose();
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
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    const Divider(),
                    const SizedBox(height: 8),
                    
                    // Scrollable content area
                    Flexible(
                      child: SingleChildScrollView(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            // Node properties
                            ..._buildProperties(),
                            
                            const SizedBox(height: 16),
                            
                            // Status indicators
                            _buildStatusIndicators(),
                          ],
                        ),
                      ),
                    ),
                    
                    const SizedBox(height: 16),
                    
                    // Actions
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        TextButton(
                          onPressed: () {
                            // This would be implemented in the future to show detailed metrics
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Detailed metrics will be implemented in the future')),
                            );
                          },
                          child: const Text('View Metrics'),
                        ),
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

  List<Widget> _buildProperties() {
    final List<Widget> propertyWidgets = [];
    
    // Add kind if available
    if (widget.node.properties.containsKey('kind')) {
      propertyWidgets.add(
        _buildPropertyRow('Kind', widget.node.properties['kind']),
      );
    }
    
    // Add IP address if available
    if (widget.node.properties.containsKey('ip')) {
      propertyWidgets.add(
        _buildPropertyRow('IP Address', widget.node.properties['ip']),
      );
    }
    
    // Add detailed properties from server response
    if (_detailedProperties.isNotEmpty) {
      propertyWidgets.add(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Text(
            'Detailed Properties',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 16,
              color: Colors.grey[800],
            ),
          ),
        ),
      );
      
      // Add each property from the detailed properties
      _detailedProperties.forEach((key, value) {
        if (value != null) {
          String displayValue;
          if (value is Map || value is List) {
            // Format complex objects
            displayValue = value.toString();
          } else {
            displayValue = value.toString();
          }
          propertyWidgets.add(_buildPropertyRow(key, displayValue));
        }
      });
    } else {
      // Add default properties based on node type if no detailed properties
      switch (widget.node.type) {
        case NodeType.route:
          propertyWidgets.add(_buildPropertyRow('Routing Protocol', 'BGP/OSPF'));
          propertyWidgets.add(_buildPropertyRow('Throughput', '10 Gbps'));
          break;
        case NodeType.subnetwork:
          propertyWidgets.add(_buildPropertyRow('Ports', '24'));
          propertyWidgets.add(_buildPropertyRow('Switching Capacity', '100 Gbps'));
          break;
        case NodeType.compute:
          propertyWidgets.add(_buildPropertyRow('CPU', '16 cores'));
          propertyWidgets.add(_buildPropertyRow('Memory', '64 GB'));
          propertyWidgets.add(_buildPropertyRow('Storage', '2 TB SSD'));
          break;
        case NodeType.firewall:
          propertyWidgets.add(_buildPropertyRow('Throughput', '5 Gbps'));
          propertyWidgets.add(_buildPropertyRow('Active Rules', '124'));
          break;
        case NodeType.network:
          propertyWidgets.add(_buildPropertyRow('Algorithm', 'Round Robin'));
          propertyWidgets.add(_buildPropertyRow('Active Connections', '1,245'));
          break;
        case NodeType.wireguard:
          propertyWidgets.add(_buildPropertyRow('Coverage', '5 km'));
          propertyWidgets.add(_buildPropertyRow('Frequency', '2.4 GHz'));
          break;
        case NodeType.ptp:
          propertyWidgets.add(_buildPropertyRow('Type', 'Omnidirectional'));
          propertyWidgets.add(_buildPropertyRow('Gain', '12 dBi'));
          break;
        case NodeType.mesh:
          propertyWidgets.add(_buildPropertyRow('Type', 'Omnidirectional'));
          propertyWidgets.add(_buildPropertyRow('Gain', '12 dBi'));
          break;
        default:
          break;
      }
    }
    
    return propertyWidgets;
  }

  Widget _buildPropertyRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: const TextStyle(
                fontWeight: FontWeight.bold,
                color: Color(0xFF0D47A1),
              ),
            ),
          ),
          Expanded(
            child: Text(value),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusIndicators() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: [
        _buildStatusIndicator('Status', 'Online', Colors.green),
        _buildStatusIndicator('CPU', '32%', Colors.orange),
        _buildStatusIndicator('Memory', '45%', Colors.orange),
      ],
    );
  }

  Widget _buildStatusIndicator(String label, String value, Color color) {
    return Column(
      children: [
        Text(
          label,
          style: TextStyle(
            fontSize: 12,
            color: Colors.grey[600],
          ),
        ),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: color.withOpacity(0.2),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: color),
          ),
          child: Text(
            value,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.bold,
            ),
          ),
        ),
      ],
    );
  }

}
