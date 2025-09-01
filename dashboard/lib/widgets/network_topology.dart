import 'package:flutter/material.dart';
import 'package:graphview/GraphView.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../appstate.dart';
import '../models/network_node.dart';
import 'NoArrowEdgeRenderer.dart';
import 'node_details_dialog.dart';

class NetworkTopologyWidget extends StatefulWidget {
  final NetworkTopology topology;

  const NetworkTopologyWidget({
    super.key,
    required this.topology,
  });

  static const defaultView = 'network';

  @override
  State<NetworkTopologyWidget> createState() => _NetworkTopologyWidgetState();
}

class _NetworkTopologyWidgetState extends State<NetworkTopologyWidget> {
  late Graph graph;
  late Algorithm algorithm;
  final Map<String, Node> nodeMap = {};
  
  // View options
  final List<String> _viewOptions = ['network', 'resources', 'both'];
  late String _selectedView;

  // Layout options
  final List<String> _layoutOptions = ['force-directed', 'layered'];
  
  // Zoom control
  final TransformationController _transformationController = TransformationController();

  // Get current view
  String getCurrentView() {
    return _selectedView;
  }

  @override
  void initState() {
    super.initState();
    // Initialize _selectedView from appstate
    final appState = Provider.of<Appstate>(context, listen: false);
    _selectedView = appState.selectedTopologyView;
    _initializeGraph();
  }
  
  @override
  void didUpdateWidget(NetworkTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    
    // Always reinitialize the graph when the widget is updated
    // This ensures proper handling of both topology and filter/layout changes
    _initializeGraph();
  }

  void _initializeGraph() {
    try {
      // Get the appstate for filtering
      final appState = Provider.of<Appstate>(context, listen: false);
      
      // Create a new graph instance to ensure clean state
      graph = Graph()..isTree = false;
      
      // Clear the node map to avoid stale references
      nodeMap.clear();
            
      // Create a filtered list of nodes
      final List<NetworkNode> filteredNodes = [];
      for (final node in widget.topology.nodes) {
        // Skip nodes that don't match the node type filter if filtering is enabled
        if (appState.filterByNodeType) {
          final bool shouldInclude = appState.selectedNodeTypes.contains(node.type);          
          if (!shouldInclude) {
            continue;
          }
        }
        
        filteredNodes.add(node);
      }
      
      // If no nodes after filtering, create an empty graph
      if (filteredNodes.isEmpty) {
        algorithm = FruchtermanReingoldAlgorithm(iterations: 1000);
        return;
      }
      
      // Add nodes to the graph
      for (final node in filteredNodes) {
        final graphNode = Node.Id(node.id);
        nodeMap[node.id] = graphNode;
        graph.addNode(graphNode);
      }
  
      // Create edges - only for nodes that exist in the current topology
      for (final connection in widget.topology.connections) {
        // Skip edges where either source or target node doesn't exist
        if (!nodeMap.containsKey(connection.sourceId) || 
            !nodeMap.containsKey(connection.targetId)) {
          continue;
        }
        
        final sourceNode = nodeMap[connection.sourceId];
        final targetNode = nodeMap[connection.targetId];
        
        if (sourceNode != null && targetNode != null) {
          // Find the corresponding network nodes to check their kind
          final sourceNetworkNode = widget.topology.nodes.firstWhere(
            (n) => n.id == connection.sourceId,
            orElse: () => NetworkNode(
              id: connection.sourceId,
              name: 'Unknown',
              type: NodeType.compute,
            ),
          );
          
          final targetNetworkNode = widget.topology.nodes.firstWhere(
            (n) => n.id == connection.targetId,
            orElse: () => NetworkNode(
              id: connection.targetId,
              name: 'Unknown',
              type: NodeType.compute,
            ),
          );
          
          // Get the kinds of both nodes
          final sourceKind = sourceNetworkNode.properties['kind'] as String?;
          final targetKind = targetNetworkNode.properties['kind'] as String?;
          
          // Remove debug print to avoid potential issues
          // print('Connection: $sourceKind -> $targetKind');
          
          // Create a paint object based on the node types
          final paint = Paint()
            ..style = PaintingStyle.stroke;
          
          // Check if this is a connection between ComputeSubnetwork and ComputeInstance
          final isComputeSubnetworkToInstance = 
              (sourceKind == 'ComputeSubnetwork' && targetKind == 'ComputeInstance') ||
              (sourceKind == 'ComputeInstance' && targetKind == 'ComputeSubnetwork');
          
          // IMPORTANT: We're using a completely different approach now
          // Instead of trying to store data in the edge, we'll use a global map to track edge types
          
          if (isComputeSubnetworkToInstance) {
            // Use a very specific blue color that won't be used elsewhere
            paint.color = const Color.fromARGB(255, 0, 0, 255); // Pure blue RGB(0,0,255)
            paint.strokeWidth = 3; // Thicker line
            
            // print('Setting BLUE line for $sourceKind -> $targetKind');
          } else {
            // Use a very specific black color that won't be used elsewhere
            paint.color = const Color.fromARGB(255, 0, 0, 0); // Pure black RGB(0,0,0)
            paint.strokeWidth = 2;
            
            // print('Setting BLACK line for $sourceKind -> $targetKind');
          }
          
          graph.addEdge(sourceNode, targetNode, paint: paint);
        }
      }
  
  // Set layout algorithm based on selection
  switch (appState.selectedTopologyLayout) {
    case 'layered':
      final configuration = SugiyamaConfiguration()
        ..nodeSeparation = 60   // Reduced from 120 to 60 for closer horizontal spacing
        ..levelSeparation = 80  // Reduced from 150 to 80 for closer vertical spacing
        ..orientation = SugiyamaConfiguration.ORIENTATION_TOP_BOTTOM;  // Explicit orientation
      algorithm = SugiyamaAlgorithm(configuration);
      algorithm.renderer = NoArrowEdgeRenderer();
      break;
    case 'force-directed':
    default:
      // Increase iterations for better node distribution
      algorithm = FruchtermanReingoldAlgorithm(
        iterations: 1500,  // Increased from 1000 to 1500
        // Use only the parameters supported by the package
        // The higher iteration count will help spread nodes more evenly
      );
      algorithm.renderer = NoArrowEdgeRenderer();
      break;
  }
    } catch (e, stackTrace) {
      print('Error initializing graph: $e');
      print('Stack trace: $stackTrace');
      // If there's an error, create an empty graph to avoid crashes
      graph = Graph()..isTree = false;
      algorithm = FruchtermanReingoldAlgorithm(iterations: 1000);
      algorithm.renderer = NoArrowEdgeRenderer();
    }
  }

  @override
  Widget build(BuildContext context) {
    // Get the appstate for filtering, but don't listen to changes
    // This prevents the widget from rebuilding on every appstate change
    final appState = Provider.of<Appstate>(context, listen: false);
    
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40, // Reduced height from 56 to 40
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0), // Reduced vertical padding
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Center(
            child: Text(
              'Network Topology',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.bold,
                color: Color(0xFF0D47A1), // Dark blue text
              ),
            ),
          ),
        ),
        // Divider removed as requested
        Expanded(
          child: widget.topology.nodes.isEmpty
              ? Center(
                  child: Text(
                    'No network nodes available',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                )
              : nodeMap.isEmpty
                ? Center(
                    child: Text(
                      'No nodes match the current filter',
                      style: TextStyle(
                        color: Colors.grey[700],
                        fontStyle: FontStyle.italic,
                      ),
                    ),
                  )
                : Stack(
                    children: [
                      InteractiveViewer(
                        transformationController: _transformationController,
                        constrained: false,
                        boundaryMargin: const EdgeInsets.all(100),
                        minScale: 0.01,
                        maxScale: 5.6,
                        child: Consumer<Appstate>(
                          builder: (context, appState, child) {
                            return GraphView(
                              key: ValueKey('graph-${nodeMap.length}-${graph.edges.length}-${appState.selectedTopologyLayout}-${appState.incidents.length}'),
                              graph: graph,
                              algorithm: algorithm,
                              paint: Paint()
                                ..color = const Color(0xFF0D47A1) // Dark blue for graph lines
                                ..strokeWidth = 1.5 // Slightly thicker lines for better visibility
                                ..style = PaintingStyle.stroke,
                              builder: (Node node) {
                                try {
                                  final nodeId = node.key!.value.toString();
                                  
                                  // Check if the node exists in our node map
                                  if (!nodeMap.containsKey(nodeId)) {
                                    // If the node doesn't exist in our map, return an empty container
                                    return Container();
                                  }
                                  
                                  // Find the corresponding network node
                                  final networkNode = widget.topology.nodes.firstWhere(
                                    (n) => n.id == nodeId,
                                    orElse: () => NetworkNode(
                                      id: nodeId,
                                      name: 'Unknown',
                                      type: NodeType.compute,
                                    ),
                                  );
                                  
                                  return _buildNodeWidget(networkNode);
                                } catch (e) {
                                  print('Error building node: $e');
                                  // Return an empty container if there's an error
                                  return Container();
                                }
                              },
                            );
                          },
                        ),
                      ),
                      // Zoom controls and filter button positioned in the top-right corner
                      Positioned(
                        top: 16,
                        right: 16,
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            // Zoom In button
                            Container(
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(8),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withOpacity(0.2),
                                    blurRadius: 4,
                                    offset: const Offset(0, 2),
                                  ),
                                ],
                              ),
                              child: IconButton(
                                icon: const Icon(Icons.zoom_in),
                                color: const Color(0xFF1976D2),
                                tooltip: 'Zoom In',
                                onPressed: _zoomIn,
                              ),
                            ),
                            const SizedBox(height: 8),
                            // Zoom Out button
                            Container(
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(8),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withOpacity(0.2),
                                    blurRadius: 4,
                                    offset: const Offset(0, 2),
                                  ),
                                ],
                              ),
                              child: IconButton(
                                icon: const Icon(Icons.zoom_out),
                                color: const Color(0xFF1976D2),
                                tooltip: 'Zoom Out',
                                onPressed: _zoomOut,
                              ),
                            ),
                            const SizedBox(height: 8),
                            // Filter button
                            Container(
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(8),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withOpacity(0.2),
                                    blurRadius: 4,
                                    offset: const Offset(0, 2),
                                  ),
                                ],
                              ),
                              child: Consumer<Appstate>(
                                builder: (context, appState, child) {
                                  return IconButton(
                                    icon: Icon(
                                      Icons.filter_list,
                                      color: appState.filterByNodeType ? Colors.amber : const Color(0xFF1976D2),
                                    ),
                                    tooltip: 'Filter nodes, view & layout options',
                                    onPressed: _showNodeTypeFilterDialog,
                                  );
                                },
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
        ),
      ],
    );
  }

  Widget _buildNodeWidget(NetworkNode node) {
    final Color color = node.getColor();
    final IconData icon = node.getIcon();
    final Color statusColor = NetworkNode.getStatusColor(node.properties['status']);
    
    // For compute instance nodes, we'll show network traffic metrics
    final bool isComputeInstance = node.type == NodeType.compute && 
                                  node.properties['kind'] == 'ComputeInstance';

    // Check if this node has any associated incidents
    final bool hasIncident = _hasMatchingIncident(node);

    return Tooltip(
      message: 'Click for details',
      child: InkWell(
        onTap: () => _showNodeDetails(node),
        child: Card(
          elevation: 0,
          shape: CircleBorder(),
          child: Container(
            padding: const EdgeInsets.all(6),
            width: 100,  // Increased width to accommodate traffic metrics
            height: 90,
            decoration: hasIncident ? BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.yellow,
              border: Border.all(
                color: Colors.red,
                width: 3,
              ),
            ) : null,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                isComputeInstance
                    ? Consumer<Appstate>(
                        builder: (context, appState, child) {
                          // Get metrics for this node
                          final nodeMetrics = appState.metrics.data[node.id];
                          final latestMetric = nodeMetrics != null && nodeMetrics.isNotEmpty 
                              ? nodeMetrics.first 
                              : null;
                          
                          // Calculate average network traffic if metrics are available
                          String trafficText = '';
                          if (latestMetric != null && latestMetric.interfaces.isNotEmpty) {
                            double totalSent = 0;
                            double totalReceived = 0;
                            int interfaceCount = 0;
                            
                            latestMetric.interfaces.forEach((name, data) {
                              final sentThroughput = data['byte_sent_throughput'] as double? ?? 0.0;
                              final recvThroughput = data['byte_recv_throughput'] as double? ?? 0.0;
                              totalSent += sentThroughput;
                              totalReceived += recvThroughput;
                              interfaceCount++;
                            });
                            
                            // Calculate average and format as human-readable
                            final avgTraffic = (totalSent + totalReceived) / 2;
                            trafficText = _formatNetworkSpeed(avgTraffic);
                          }
                          
                          return Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(icon, color: color, size: 20),
                              const SizedBox(width: 4),
                              Container(
                                width: 8,
                                height: 8,
                                decoration: BoxDecoration(
                                  color: statusColor,
                                  shape: BoxShape.circle,
                                ),
                              ),
                              if (trafficText.isNotEmpty) ...[
                                const SizedBox(width: 4),
                                Flexible(
                                  child: Text(
                                    trafficText,
                                    style: const TextStyle(
                                      fontSize: 8,  // Reduced font size
                                      fontWeight: FontWeight.bold,
                                    ),
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                              ],
                            ],
                          );
                        },
                      )
                    : Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(icon, color: color, size: 20),
                          const SizedBox(width: 4),
                          Container(
                            width: 8,
                            height: 8,
                            decoration: BoxDecoration(
                              color: statusColor,
                              shape: BoxShape.circle,
                            ),
                          ),
                        ],
                      ),
                const SizedBox(height: 2),
                Text(
                  node.name,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
                Text(
                  node.properties['kind'] ?? '',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 9, fontStyle: FontStyle.italic),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
                Text(
                  node.properties['ip'] ?? '',
                  style: const TextStyle(fontSize: 9, color: Colors.grey),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // Update topology graph view
  void _getTopologyView(String view) {
    // Use appstate to get topology view
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.getTopologyView(view);
  }

  void _showNodeDetails(NetworkNode node) {
    // Use appstate to get node details
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.getNodeDetails(node.id);
    
    // Show dialog with loading state
    showDialog(
      context: context,
      builder: (BuildContext context) => NodeDetailsDialog(
        node: node,
      ),
    );
  }
  
  // Zoom methods
  void _zoomIn() {
    final Matrix4 currentTransform = _transformationController.value;
    final double currentScale = currentTransform.getMaxScaleOnAxis();
    final double newScale = (currentScale * 1.2).clamp(0.01, 5.6);
    
    // Create a new transformation matrix with the new scale
    final Matrix4 newTransform = Matrix4.identity()..scale(newScale);
    
    _transformationController.value = newTransform;
  }

  void _zoomOut() {
    final Matrix4 currentTransform = _transformationController.value;
    final double currentScale = currentTransform.getMaxScaleOnAxis();
    final double newScale = (currentScale / 1.2).clamp(0.01, 5.6);
    
    // Create a new transformation matrix with the new scale
    final Matrix4 newTransform = Matrix4.identity()..scale(newScale);
    
    _transformationController.value = newTransform;
  }

  // Check if a node has any matching incidents
  bool _hasMatchingIncident(NetworkNode node) {
    // Only apply red borders to ComputeInstance nodes
    if (node.type != NodeType.compute || node.properties['kind'] != 'ComputeInstance') {
      return false;
    }
    
    final appState = Provider.of<Appstate>(context, listen: false);
    
    // Get all open incidents
    final incidents = appState.incidents;
    
    // Check if any incident title exactly matches this node's name
    for (final incident in incidents) {
      // Only consider unresolved incidents
      if (incident.state != 'resolved') {
        // Compare incident title with node name (case-insensitive, exact match only)
        final incidentTitle = incident.title.toLowerCase().trim();
        final nodeName = node.name.toLowerCase().trim();
        
        // Check for exact match only
        if (incidentTitle == nodeName) {
          return true;
        }
      }
    }
    
    return false;
  }

  // Simple dialog to filter nodes by type
  // Helper method to format network speed in a human-readable format
  String _formatNetworkSpeed(double bytesPerSecond) {
    if (bytesPerSecond < 1024) {
      return '${bytesPerSecond.toStringAsFixed(0)}B/s';  // More compact format
    } else if (bytesPerSecond < 1024 * 1024) {
      return '${(bytesPerSecond / 1024).toStringAsFixed(1)}K/s';  // More compact format
    } else {
      return '${(bytesPerSecond / (1024 * 1024)).toStringAsFixed(1)}M/s';  // More compact format
    }
  }
  
  void _showNodeTypeFilterDialog() {
    // Get the appstate for filtering
    final appState = Provider.of<Appstate>(context, listen: false);
    
    // Create temporary variables for the dialog
    Set<NodeType> tempSelection = Set<NodeType>.from(appState.selectedNodeTypes);
    String tempSelectedView = _selectedView;
    String tempSelectedLayout = appState.selectedTopologyLayout;
    
    // Show a simple dialog
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            // Get sorted list of node types
            List<NodeType> sortedNodeTypes = NodeType.values.toList();
            
            return AlertDialog(
              title: Text('Update Topology View'),
              content: SizedBox(
                width: 350,
                height: 400,
                child: Column(
                  children: [
                    // View and Layout dropdowns on the same row at the top
                    Row(
                      children: [
                        // View dropdown
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'View:',
                                style: TextStyle(
                                  color: Color(0xFF0D47A1),
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Container(
                                height: 40,
                                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 0),
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: Color(0xFF1976D2)),
                                ),
                                child: DropdownButton<String>(
                                  value: tempSelectedView,
                                  icon: const Icon(Icons.arrow_drop_down, color: Color(0xFF1976D2), size: 20),
                                  elevation: 16,
                                  isDense: true,
                                  style: const TextStyle(color: Color(0xFF0D47A1)),
                                  underline: Container(height: 0),
                                  isExpanded: true,
                                  onChanged: (String? newValue) {
                                    if (newValue != null) {
                                      setDialogState(() {
                                        tempSelectedView = newValue;
                                      });
                                    }
                                  },
                                  items: _viewOptions.map<DropdownMenuItem<String>>((String value) {
                                    String displayText = value[0].toUpperCase() + value.substring(1);
                                    
                                    return DropdownMenuItem<String>(
                                      value: value,
                                      child: Text(
                                        displayText,
                                        style: const TextStyle(
                                          fontSize: 14,
                                          fontWeight: FontWeight.bold,
                                        ),
                                      ),
                                    );
                                  }).toList(),
                                ),
                              ),
                            ],
                          ),
                        ),
                        
                        const SizedBox(width: 16),
                        
                        // Layout dropdown
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Layout:',
                                style: TextStyle(
                                  color: Color(0xFF0D47A1),
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Container(
                                height: 40,
                                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 0),
                                decoration: BoxDecoration(
                                  color: Colors.white,
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: Color(0xFF1976D2)),
                                ),
                                child: DropdownButton<String>(
                                  value: tempSelectedLayout,
                                  icon: const Icon(Icons.arrow_drop_down, color: Color(0xFF1976D2), size: 20),
                                  elevation: 16,
                                  isDense: true,
                                  style: const TextStyle(color: Color(0xFF0D47A1)),
                                  underline: Container(height: 0),
                                  isExpanded: true,
                                  onChanged: (String? newValue) {
                                    if (newValue != null) {
                                      setDialogState(() {
                                        tempSelectedLayout = newValue;
                                      });
                                    }
                                  },
                                  items: _layoutOptions.map<DropdownMenuItem<String>>((String value) {
                                    String displayText = value.split('-').map((word) => 
                                      word[0].toUpperCase() + word.substring(1)
                                    ).join(' ');
                                    
                                    return DropdownMenuItem<String>(
                                      value: value,
                                      child: Text(
                                        displayText,
                                        style: const TextStyle(
                                          fontSize: 14,
                                          fontWeight: FontWeight.bold,
                                        ),
                                      ),
                                    );
                                  }).toList(),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    
                    // Add divider
                    SizedBox(height: 16),
                    Divider(thickness: 1),
                    SizedBox(height: 8),
                    
                    Text('Select which node types to display:'),
                    SizedBox(height: 8),
                    Expanded(
                      child: ListView(
                        children: sortedNodeTypes.map((nodeType) {
                          bool isSelected = tempSelection.contains(nodeType);
                          return CheckboxListTile(
                            title: Text(nodeType.toString().split('.').last),
                            value: isSelected,
                            onChanged: (bool? value) {
                              setDialogState(() {
                                if (value == true) {
                                  tempSelection.add(nodeType);
                                } else {
                                  tempSelection.remove(nodeType);
                                }
                              });
                            },
                          );
                        }).toList(),
                      ),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  child: Text('Cancel'),
                  onPressed: () {
                    Navigator.of(context).pop();
                  },
                ),
                TextButton(
                  child: Text('Select All'),
                  onPressed: () {
                    setDialogState(() {
                      tempSelection = Set<NodeType>.from(NodeType.values);
                    });
                  },
                ),
                TextButton(
                  child: Text('Clear All'),
                  onPressed: () {
                    setDialogState(() {
                      tempSelection.clear();
                    });
                  },
                ),
                TextButton(
                  child: Text('Apply'),
                  onPressed: () {
                    try {
                      // Close dialog first
                      Navigator.of(context).pop();
                      
                      // Create a new set to avoid reference issues
                      Set<NodeType> newSelection = Set<NodeType>.from(tempSelection);
                      
                      // Update the filter state in appstate
                      appState.updateTopologyFiltering(
                        filterByNodeType: newSelection.length < NodeType.values.length,
                        selectedNodeTypes: newSelection,
                      );
                      
                      // Update view if changed
                      if (tempSelectedView != _selectedView) {
                        setState(() {
                          _selectedView = tempSelectedView;
                        });
                        _getTopologyView(_selectedView);
                      }
                      
                      // Update layout if changed
                      if (tempSelectedLayout != appState.selectedTopologyLayout) {
                        appState.updateTopologyLayout(tempSelectedLayout);
                      }
                      
                      // Show feedback
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text(
                            appState.filterByNodeType 
                                ? 'Showing ${newSelection.length} of ${NodeType.values.length} node types' 
                                : 'Showing all node types'
                          ),
                          duration: Duration(seconds: 2),
                        ),
                      );
                    } catch (e, stackTrace) {
                      print('Error applying filter: $e');
                      print('Stack trace: $stackTrace');
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text('Error applying filter'),
                          backgroundColor: Colors.red,
                          duration: Duration(seconds: 3),
                        ),
                      );
                    }
                  },
                ),
              ],
            );
          },
        );
      },
    );
  }
}
