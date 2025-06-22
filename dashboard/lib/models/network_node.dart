import 'package:flutter/material.dart';

enum NodeType {
  network,
  subnetwork,
  compute,
  firewall,
  address,
  route,
  config,
  wireguard,
  ptp,
  mesh,
  cluster,
  upf,
  controlplane,
  ueransim,
  uetest
}

class NetworkNode {
  final String id;
  final String name;
  final NodeType type;
  final Map<String, dynamic> properties;

  NetworkNode({
    required this.id,
    required this.name,
    required this.type,
    this.properties = const {},
  });
  
  // Get the appropriate icon for this node type
  IconData getIcon() {
    switch (type) {
      case NodeType.route:
        return Icons.router;
      case NodeType.network:
        return Icons.device_hub;
      case NodeType.subnetwork:
        return Icons.storage;
      case NodeType.compute:
        return Icons.computer;
      case NodeType.firewall:
        return Icons.security;
      case NodeType.address:
        return Icons.network_ping;
      case NodeType.wireguard:
        return Icons.vpn_lock;
      case NodeType.config:
        return Icons.edit_document;
      case NodeType.ptp:
        return Icons.private_connectivity_rounded;
      case NodeType.mesh:
        return Icons.auto_graph_rounded;
      case NodeType.cluster:
        return Icons.cloud_sharp;
      case NodeType.upf:
        return Icons.router_sharp;
      case NodeType.controlplane:
        return Icons.control_point;
      case NodeType.ueransim:
        return Icons.wifi_rounded;
      case NodeType.uetest:
        return Icons.textsms_sharp;
      default:
        return Icons.device_unknown;
    }
  }
  
  // Get the appropriate color for this node type
  Color getColor() {
    switch (type) {
      case NodeType.network:
        return const Color(0xFF0D47A1); // Dark blue
      case NodeType.subnetwork:
        return const Color(0xFF1976D2); // Medium blue
      case NodeType.compute:
        return const Color(0xFF2196F3); // Standard blue
      case NodeType.route:
        return const Color(0xFF42A5F5); // Light blue
      case NodeType.firewall:
        return const Color(0xFF1565C0); // Deep blue
      case NodeType.wireguard:
        return const Color(0xFF0277BD); // Ocean blue
      case NodeType.ptp:
        return const Color(0xFF0288D1); // Bright blue
      case NodeType.mesh:
        return const Color(0xFF01579B); // Dark ocean blue
      default:
        return const Color(0xFF90CAF9); // Very light blue
    }
  }
  
  // Get the appropriate color for a node status
  static Color getStatusColor(String? status) {
    if (status == null) {
      return Colors.grey; // Default color for unknown status
    }
    
    switch (status.toLowerCase()) {
      case 'error':
        return Colors.red;
      case 'initializing':
      case 'starting':
      case 'starting up':
      case 'running':
      case 'updatefailed':
      case 'uptodate':
      case 'updating':
        return Colors.green; // Green for UpToDate nodes
      default:
        return Colors.grey; // Default color for unknown status
    }
  }
  
  // Map the kind from the server to a NodeType
  static NodeType mapKindToNodeType(String kind) {
    switch (kind) {
      case 'ComputeNetwork':
        return NodeType.network;
      case 'ComputeSubnetwork':
        return NodeType.subnetwork;
      case 'ComputeFirewall':
        return NodeType.firewall;
      case 'ComputeInstance':
        return NodeType.compute;
      case 'ComputeRoute':
        return NodeType.route;
      case 'ComputeAddress':
        return NodeType.address;
      case 'ConfigMap':
        return NodeType.config;
      case 'WireguardAppliance':
        return NodeType.wireguard;
      case 'PointToPointService':
        return NodeType.ptp;
      case 'MeshService':
        return NodeType.mesh;
      case 'ContainerCluster':
        return NodeType.cluster;
      case 'UserPlaneFunction':
        return NodeType.upf;
      case 'ControlPlane':
        return NodeType.controlplane;
      case 'UERanSim':
        return NodeType.ueransim;
      case 'UETest':
        return NodeType.uetest;
      default:
        return NodeType.compute;
    }
  }
}

class NetworkConnection {
  final String id;
  final String sourceId;
  final String targetId;
  final String label;
  final Map<String, dynamic> properties;

  NetworkConnection({
    required this.id,
    required this.sourceId,
    required this.targetId,
    this.label = '',
    this.properties = const {},
  });
}

class NetworkTopology {
  final List<NetworkNode> nodes;
  final List<NetworkConnection> connections;

  NetworkTopology({
    required this.nodes,
    required this.connections,
  });
  
  // Create an empty topology with no nodes or connections
  NetworkTopology.empty()
      : nodes = [],
        connections = [];
  
  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    if (other is! NetworkTopology) return false;
    
    // Compare nodes and connections lengths
    if (nodes.length != other.nodes.length || 
        connections.length != other.connections.length) {
      // print('Topology comparison: Different lengths');
      return false;
    }
    
    // Create maps of nodes by ID for more efficient comparison
    final thisNodesMap = <String, NetworkNode>{};
    final otherNodesMap = <String, NetworkNode>{};
    
    for (var node in nodes) {
      thisNodesMap[node.id] = node;
    }
    
    for (var node in other.nodes) {
      otherNodesMap[node.id] = node;
    }
    
    // Check if both topologies have the same node IDs
    if (!thisNodesMap.keys.toSet().containsAll(otherNodesMap.keys) ||
        !otherNodesMap.keys.toSet().containsAll(thisNodesMap.keys)) {
      // print('Topology comparison: Different node IDs');
      return false;
    }
    
    // Compare each node by ID, name, type, and properties
    for (final id in thisNodesMap.keys) {
      final thisNode = thisNodesMap[id]!;
      final otherNode = otherNodesMap[id]!;
      
      if (thisNode.name != otherNode.name || thisNode.type != otherNode.type) {
        // print('Topology comparison: Node $id has different name or type');
        return false;
      }
      
      // Compare properties
      if (thisNode.properties.length != otherNode.properties.length) {
        // print('Topology comparison: Node $id has different property count');
        return false;
      }
      
      for (final key in thisNode.properties.keys) {
        if (!otherNode.properties.containsKey(key) ||
            thisNode.properties[key] != otherNode.properties[key]) {
          // print('Topology comparison: Node $id has different property $key');
          return false;
        }
      }
    }
    
    // Create maps of connections by source and target for more efficient comparison
    final thisConnectionsMap = <String, NetworkConnection>{};
    final otherConnectionsMap = <String, NetworkConnection>{};
    
    for (var conn in connections) {
      final key = '${conn.sourceId}-${conn.targetId}';
      thisConnectionsMap[key] = conn;
    }
    
    for (var conn in other.connections) {
      final key = '${conn.sourceId}-${conn.targetId}';
      otherConnectionsMap[key] = conn;
    }
    
    // Check if both topologies have the same connection keys
    if (!thisConnectionsMap.keys.toSet().containsAll(otherConnectionsMap.keys) ||
        !otherConnectionsMap.keys.toSet().containsAll(thisConnectionsMap.keys)) {
      // print('Topology comparison: Different connection pairs');
      return false;
    }
    
    // Compare each connection by source, target, label, and properties
    for (final key in thisConnectionsMap.keys) {
      final thisConn = thisConnectionsMap[key]!;
      final otherConn = otherConnectionsMap[key]!;
      
      if (thisConn.label != otherConn.label) {
        // print('Topology comparison: Connection $key has different label');
        return false;
      }
      
      // Compare properties
      if (thisConn.properties.length != otherConn.properties.length) {
        // print('Topology comparison: Connection $key has different property count');
        return false;
      }
      
      for (final propKey in thisConn.properties.keys) {
        if (!otherConn.properties.containsKey(propKey) ||
            thisConn.properties[propKey] != otherConn.properties[propKey]) {
          // print('Topology comparison: Connection $key has different property $propKey');
          return false;
        }
      }
    }
    
    return true;
  }
  
  @override
  int get hashCode {
    // Create a more robust hashCode that doesn't depend on the order of nodes and connections
    final nodesHash = nodes.fold(0, (hash, node) => hash ^ node.id.hashCode);
    final connectionsHash = connections.fold(0, (hash, conn) => 
        hash ^ '${conn.sourceId}-${conn.targetId}'.hashCode);
    return nodesHash ^ connectionsHash;
  }

}
