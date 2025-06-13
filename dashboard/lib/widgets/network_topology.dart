import 'package:flutter/material.dart';
import 'package:graphview/GraphView.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../appstate.dart';
import '../models/network_node.dart';
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
  String _selectedView = NetworkTopologyWidget.defaultView;

  // Layout options
  final List<String> _layoutOptions = ['force-directed', 'layered'];

  // Default network view
  String defaultView() {
    return(_selectedView);
  }

  @override
  void initState() {
    super.initState();
    _initializeGraph();
  }
  
  @override
  void didUpdateWidget(NetworkTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    
    final topologyChanged = widget.topology != oldWidget.topology;    
    if (topologyChanged) {
      _initializeGraph();
    }
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
          graph.addEdge(sourceNode, targetNode, paint: Paint()
            ..color = const Color(0xFF1976D2) // Medium blue for edges
            ..strokeWidth = 2);
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
      break;
    case 'force-directed':
    default:
      // Increase iterations for better node distribution
      algorithm = FruchtermanReingoldAlgorithm(
        iterations: 1500,  // Increased from 1000 to 1500
        // Use only the parameters supported by the package
        // The higher iteration count will help spread nodes more evenly
      );
      break;
  }
    } catch (e, stackTrace) {
      print('Error initializing graph: $e');
      print('Stack trace: $stackTrace');
      // If there's an error, create an empty graph to avoid crashes
      graph = Graph()..isTree = false;
      algorithm = FruchtermanReingoldAlgorithm(iterations: 1000);
    }
  }

  @override
  Widget build(BuildContext context) {
    // Get the appstate for filtering
    final appState = Provider.of<Appstate>(context);
    
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
          child: LayoutBuilder(
            builder: (context, constraints) {
              // Calculate if we have enough space for centered title
              // Controls width estimate (filter + spacing + view dropdown + spacing + layout dropdown)
              final controlsWidth = 32 + 16 + 120 + 16 + 120; // Approximate width of all controls
              final hasEnoughSpace = constraints.maxWidth > (controlsWidth * 1.5); // 1.5x multiplier for comfortable spacing
              
              return Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  // Title - either centered in its own space or aligned to the left
                  if (hasEnoughSpace)
                    Expanded(
                      child: Center(
                        child: Text(
                          'Network Topology',
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
                        'Network Topology',
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
                    // Filter icon button
                    Container(
                      height: 32,
                      width: 32,
                      decoration: BoxDecoration(
                        color: appState.filterByNodeType ? Color(0xFF1976D2) : Colors.white,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: Color(0xFF1976D2)),
                      ),
                      child: IconButton(
                        icon: Icon(
                          Icons.filter_list,
                          color: appState.filterByNodeType ? Colors.white : Color(0xFF1976D2),
                          size: 18,
                        ),
                        padding: EdgeInsets.zero,
                        tooltip: 'Filter by node type',
                        onPressed: _showNodeTypeFilterDialog,
                      ),
                    ),


                    // Add some space between filter and view
                    const SizedBox(width: 16),

                    // View dropdown
                    Row(
                      children: [
                        Text(
                          'View: ',
                          style: TextStyle(
                            color: Color(0xFF0D47A1),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(width: 8),
                        Container(
                          height: 32, // Set a fixed height for the container
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                          decoration: BoxDecoration(
                            color: Colors.white,
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: Color(0xFF1976D2)),
                          ),
                          child: DropdownButton<String>(
                            value: _selectedView,
                            icon: const Icon(Icons.arrow_drop_down, color: Color(0xFF1976D2), size: 20),
                            elevation: 16,
                            isDense: true, // Make the dropdown more compact
                            style: const TextStyle(color: Color(0xFF0D47A1)),
                            underline: Container(height: 0), // Remove the default underline
                            iconSize: 20, // Smaller icon
                            onChanged: (String? newValue) {
                              if (newValue != null && newValue != _selectedView) {
                                setState(() {
                                  _selectedView = newValue;
                                  // Reinitialize the graph with the new view
                                  _getTopologyView(_selectedView);
                                });
                              }
                            },
                            items: _viewOptions.map<DropdownMenuItem<String>>((String value) {
                              // Convert view option to display text
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

                    // Add some space between filter and layout
                    const SizedBox(width: 16),
                    
                    // Layout dropdown
                    Row(
                      children: [
                        Text(
                          'Layout: ',
                          style: TextStyle(
                            color: Color(0xFF0D47A1),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(width: 8),
                        Container(
                          height: 32, // Set a fixed height for the container
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                          decoration: BoxDecoration(
                            color: Colors.white,
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: Color(0xFF1976D2)),
                          ),
                          child: DropdownButton<String>(
                            value: appState.selectedTopologyLayout,
                            icon: const Icon(Icons.arrow_drop_down, color: Color(0xFF1976D2), size: 20),
                            elevation: 16,
                            isDense: true, // Make the dropdown more compact
                            style: const TextStyle(color: Color(0xFF0D47A1)),
                            underline: Container(height: 0), // Remove the default underline
                            iconSize: 20, // Smaller icon
                            onChanged: (String? newValue) {
                              if (newValue != null && newValue != appState.selectedTopologyLayout) {
                                // Update the layout in appstate
                                appState.updateTopologyLayout(newValue);
                                // Reinitialize the graph with the new layout
                                _initializeGraph();
                              }
                            },
                            items: _layoutOptions.map<DropdownMenuItem<String>>((String value) {
                              // Convert layout option to display text
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
                    ],
                  ),
                ],
              );
            },
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
                : InteractiveViewer(
                    constrained: false,
                    boundaryMargin: const EdgeInsets.all(100),
                    minScale: 0.01,
                    maxScale: 5.6,
                    child: GraphView(
                      key: ValueKey('graph-${nodeMap.length}-${graph.edges.length}'),
                      graph: graph,
                      algorithm: algorithm,
                      paint: Paint()
                        ..color = const Color(0xFF0D47A1) // Dark blue for graph lines
                        ..strokeWidth = 1
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
                    ),
                  ),
        ),
      ],
    );
  }

  Widget _buildNodeWidget(NetworkNode node) {
    final Color color = node.getColor();
    final IconData icon = node.getIcon();
    final Color statusColor = NetworkNode.getStatusColor(node.properties['status']);

    return Tooltip(
      message: 'Click for details',
      child: InkWell(
        onTap: () => _showNodeDetails(node),
        child: Card(
          elevation: 4,
          shape: CircleBorder(
            side: BorderSide(
              color: statusColor,
              width: 2.0,
            ),
          ),
          child: Container(
            padding: const EdgeInsets.all(6),
            width: 90,  // Equal width and height for a perfect circle
            height: 90,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, color: color, size: 20),
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
  
  // Simple dialog to filter nodes by type
  void _showNodeTypeFilterDialog() {
    // Get the appstate for filtering
    final appState = Provider.of<Appstate>(context, listen: false);
    
    // Create a temporary set for the dialog
    Set<NodeType> tempSelection = Set<NodeType>.from(appState.selectedNodeTypes);
    
    // Show a simple dialog
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            // Get sorted list of node types
            List<NodeType> sortedNodeTypes = NodeType.values.toList();
            
            return AlertDialog(
              title: Text('Filter Nodes by Type'),
              content: SizedBox(
                width: 300,
                height: 300,
                child: Column(
                  children: [
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
                      
                      // Reinitialize the graph with the new filter
                      WidgetsBinding.instance.addPostFrameCallback((_) {
                        _initializeGraph();
                      });
                      
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
