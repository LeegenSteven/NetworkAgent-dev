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
      // case 'error':
      //   return Colors.red; // Red for nodes in error
      case 'updatefailed':
      case 'starting':
      case 'running':
      case 'starting up':
      case 'initializing':
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
      return false;
    }
    
    // Compare each node by ID
    for (int i = 0; i < nodes.length; i++) {
      if (nodes[i].id != other.nodes[i].id ||
          nodes[i].name != other.nodes[i].name ||
          nodes[i].type != other.nodes[i].type) {
        return false;
      }
      
      // Compare properties
      if (nodes[i].properties.length != other.nodes[i].properties.length) {
        return false;
      }
      
      for (final key in nodes[i].properties.keys) {
        if (!other.nodes[i].properties.containsKey(key) ||
            nodes[i].properties[key] != other.nodes[i].properties[key]) {
          return false;
        }
      }
    }
    
    // Compare each connection by ID
    for (int i = 0; i < connections.length; i++) {
      if (connections[i].id != other.connections[i].id ||
          connections[i].sourceId != other.connections[i].sourceId ||
          connections[i].targetId != other.connections[i].targetId ||
          connections[i].label != other.connections[i].label) {
        return false;
      }
      
      // Compare properties
      if (connections[i].properties.length != other.connections[i].properties.length) {
        return false;
      }
      
      for (final key in connections[i].properties.keys) {
        if (!other.connections[i].properties.containsKey(key) ||
            connections[i].properties[key] != other.connections[i].properties[key]) {
          return false;
        }
      }
    }
    
    return true;
  }
  
  @override
  int get hashCode => Object.hash(
    Object.hashAll(nodes),
    Object.hashAll(connections),
  );

}
