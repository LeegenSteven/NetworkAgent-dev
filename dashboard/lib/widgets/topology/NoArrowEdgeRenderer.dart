import 'package:flutter/material.dart';
import 'package:graphview/GraphView.dart';
import 'dart:math';
import 'dart:ui' as ui;

class NoArrowEdgeRenderer extends EdgeRenderer {
  @override
  void render(Canvas canvas, Graph graph, Paint paint) {
    // Default paint for edges (will be overridden by edge-specific paint)
    final edgePaint = Paint()
      ..color = const Color(0xFF1976D2) // Medium blue for edges
      ..strokeWidth = 2.0
      ..style = PaintingStyle.stroke;

    graph.edges.forEach((edge) {
      var source = edge.source;
      var destination = edge.destination;

      // The node size is 90x90 as defined in _buildNodeWidget
      const nodeSize = 90.0;
      
      // Calculate the exact center of the source node
      // Use a fixed offset of nodeSize/2 from the node position
      var sourceCenter = Offset(
        source.x + nodeSize / 2,
        source.y + nodeSize / 2
      );
      
      // Calculate the exact center of the destination node
      // Use a fixed offset of nodeSize/2 from the node position
      var destinationCenter = Offset(
        destination.x + nodeSize / 2,
        destination.y + nodeSize / 2
      );

      // Use the edge's paint if available, otherwise use our custom paint
      final linePaint = edge.paint ?? edgePaint;
      
      // Check if this is a connection between ComputeSubnetwork and ComputeInstance
      // We're using a specific blue color (RGB 0,0,255) to identify these connections
      final isComputeSubnetworkToInstance = linePaint.color.value == const Color.fromARGB(255, 0, 0, 255).value;
      
      // Remove debug print to avoid potential issues
      // print('Edge color: 0x${linePaint.color.value.toRadixString(16).padLeft(8, '0')}, isComputeSubnetworkToInstance: $isComputeSubnetworkToInstance');
      
      if (isComputeSubnetworkToInstance) {
        // Draw a thick solid blue line for ComputeSubnetwork-ComputeInstance connections
        // Use the paint from the edge which has the correct thickness
        canvas.drawLine(sourceCenter, destinationCenter, linePaint);
      } else {
        // Draw a dotted black line for other connections
        _drawDashedLine(
          canvas,
          sourceCenter,
          destinationCenter,
          Paint()
            ..color = Colors.black
            ..strokeWidth = 2.0
            ..style = PaintingStyle.stroke,
        );
      }
    });
  }
  
  // Helper method to draw a dashed line
  void _drawDashedLine(Canvas canvas, Offset start, Offset end, Paint paint) {
    // Calculate the distance and direction vector
    final dx = end.dx - start.dx;
    final dy = end.dy - start.dy;
    final distance = sqrt(dx * dx + dy * dy);
    
    // Define dash pattern (dash length, gap length)
    const double dashLength = 5;
    const double gapLength = 3;
    
    // Calculate how many segments we need
    final count = (distance / (dashLength + gapLength)).floor();
    
    // Calculate the normalized direction vector
    final double nx = dx / distance;
    final double ny = dy / distance;
    
    // Draw the dashed line
    var startX = start.dx;
    var startY = start.dy;
    
    for (int i = 0; i < count; i++) {
      // Calculate dash start and end points
      final dashStart = Offset(startX, startY);
      final dashEnd = Offset(
        startX + nx * dashLength,
        startY + ny * dashLength,
      );
      
      // Draw the dash
      canvas.drawLine(dashStart, dashEnd, paint);
      
      // Move to the next dash start position
      startX = dashStart.dx + nx * (dashLength + gapLength);
      startY = dashStart.dy + ny * (dashLength + gapLength);
    }
    
    // Draw the final dash if there's remaining distance
    final remainingDistance = distance - count * (dashLength + gapLength);
    if (remainingDistance > 0) {
      final finalDashLength = min(remainingDistance, dashLength);
      final dashStart = Offset(startX, startY);
      final dashEnd = Offset(
        startX + nx * finalDashLength,
        startY + ny * finalDashLength,
      );
      canvas.drawLine(dashStart, dashEnd, paint);
    }
  }
}
