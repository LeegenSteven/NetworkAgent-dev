import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'dart:math' as math;
import '../appstate.dart';

enum QoELevel { poor, good, excellent }

class QoEGaugesWidget extends StatelessWidget {
  const QoEGaugesWidget({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        final qoeResult = _calculateQoEData(appState);
        final qoeData = qoeResult['data'] as Map<String, double>;
        final hasMetrics = qoeResult['hasMetrics'] as Map<String, bool>;
        
        return Container(
          padding: const EdgeInsets.all(16.0),
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
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Title
              Row(
                children: [
                  Icon(
                    Icons.speed,
                    color: const Color(0xFF0D47A1),
                    size: 20,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'Quality of Experience',
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      color: const Color(0xFF0D47A1),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              
              // Gauges Row
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  Expanded(
                    child: _buildServiceGauge(
                      'Broadband',
                      qoeData['broadband']!,
                      Colors.blue,
                      Icons.wifi,
                      hasMetrics['broadband']!,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: _buildServiceGauge(
                      'Video',
                      qoeData['video']!,
                      Colors.red,
                      Icons.videocam,
                      hasMetrics['video']!,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: _buildServiceGauge(
                      'Voice',
                      qoeData['voice']!,
                      Colors.green,
                      Icons.phone,
                      hasMetrics['voice']!,
                    ),
                  ),
                ],
              ),
              
              const SizedBox(height: 16),
              
              // Legend
              _buildLegend(),
            ],
          ),
        );
      },
    );
  }

  Widget _buildServiceGauge(String serviceName, double value, Color color, IconData icon, bool hasMetrics) {
    final level = _getQoELevel(value);
    final levelColor = _getQoELevelColor(level);
    
    return Column(
      children: [
        // Service Icon and Name
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 16, color: color),
            const SizedBox(width: 4),
            Text(
              serviceName,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: Colors.grey[700],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        
        // Gauge
        SizedBox(
          width: 80,
          height: 80,
          child: CustomPaint(
            painter: _GaugePainter(
              value: value,
              color: color,
              levelColor: levelColor,
            ),
            child: Center(
              child: !hasMetrics 
                ? const SizedBox.shrink() // Show nothing when no metrics available for this service
                : Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        '${(value * 100).toInt()}%',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.bold,
                          color: levelColor,
                        ),
                      ),
                      Text(
                        _getQoELevelText(level),
                        style: TextStyle(
                          fontSize: 8,
                          fontWeight: FontWeight.w500,
                          color: levelColor,
                        ),
                      ),
                    ],
                  ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildLegend() {
    return Container(
      padding: const EdgeInsets.all(12.0),
      decoration: BoxDecoration(
        color: Colors.grey[50],
        borderRadius: BorderRadius.circular(8.0),
        border: Border.all(color: Colors.grey[200]!, width: 1),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          _buildLegendItem('Poor', Colors.red, '0-40%'),
          _buildLegendItem('Good', Colors.orange, '41-75%'),
          _buildLegendItem('Excellent', Colors.green, '76-100%'),
        ],
      ),
    );
  }

  Widget _buildLegendItem(String label, Color color, String range) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 6),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w600,
                color: Colors.grey[700],
              ),
            ),
            Text(
              range,
              style: TextStyle(
                fontSize: 8,
                color: Colors.grey[500],
              ),
            ),
          ],
        ),
      ],
    );
  }

  Map<String, dynamic> _calculateQoEData(Appstate appState) {
    // Use actual service performance data from the supervisor
    final serviceTypes = appState.metrics.availableServiceTypes;
    Map<String, double> qoeData = {};
    Map<String, bool> hasMetrics = {
      'broadband': false,
      'video': false,
      'voice': false,
    };
    
    print('QoE Widget: Available service types: $serviceTypes');
    print('QoE Widget: Service performance data: ${appState.metrics.servicePerformance}');
    
    // If we have service performance data, use it
    if (serviceTypes.isNotEmpty) {
      for (final serviceType in serviceTypes) {
        final serviceData = appState.metrics.getServicePerformance(serviceType);
        if (serviceData != null) {
          print('QoE Widget: Processing service type: $serviceType with data: $serviceData');
          
          // Map the service type to our display names
          final mappedServiceType = _mapServiceType(serviceType);
          qoeData[mappedServiceType] = _calculateServiceQoE(serviceType, serviceData);
          hasMetrics[mappedServiceType] = true;
          
          print('QoE Widget: Mapped $serviceType -> $mappedServiceType with QoE: ${qoeData[mappedServiceType]}');
        }
      }
    }
    
    // Ensure we always have the three main service types for display
    // Set to zero if no metrics available for that service
    qoeData.putIfAbsent('broadband', () => 0.0);
    qoeData.putIfAbsent('video', () => 0.0);
    qoeData.putIfAbsent('voice', () => 0.0);
    
    print('QoE Widget: Final QoE data: $qoeData');
    print('QoE Widget: Has metrics: $hasMetrics');
    
    return {
      'data': qoeData,
      'hasMetrics': hasMetrics,
    };
  }

  /// Maps backend service type names to frontend display names
  String _mapServiceType(String backendServiceType) {
    final normalizedType = backendServiceType.toLowerCase().trim();
    
    switch (normalizedType) {
      case 'web':
      case 'broadband':
      case 'internet':
      case 'http':
      case 'https':
        return 'broadband';
      case 'video':
      case 'streaming':
      case 'media':
      case 'rtmp':
      case 'hls':
        return 'video';
      case 'voice':
      case 'voip':
      case 'sip':
      case 'audio':
      case 'call':
        return 'voice';
      default:
        // For unknown service types, try to map based on common patterns
        if (normalizedType.contains('web') || normalizedType.contains('http')) {
          return 'broadband';
        } else if (normalizedType.contains('video') || normalizedType.contains('stream')) {
          return 'video';
        } else if (normalizedType.contains('voice') || normalizedType.contains('audio')) {
          return 'voice';
        }
        // Default to broadband for unknown types
        return 'broadband';
    }
  }

  double _calculateServiceQoE(String serviceType, Map<String, dynamic> serviceData) {
    final avgResponseTime = (serviceData['avg_response_time_ms'] as num?)?.toDouble() ?? 0.0;
    final errorRate = (serviceData['error_rate'] as num?)?.toDouble() ?? 0.0;
    final totalRequests = (serviceData['total_requests'] as num?)?.toInt() ?? 0;
    
    print('QoE Widget: Calculating QoE for $serviceType - Response Time: ${avgResponseTime}ms, Error Rate: ${errorRate}%, Requests: $totalRequests');
    
    // Map the service type to get the correct thresholds
    final mappedServiceType = _mapServiceType(serviceType);
    
    // Calculate response time score (lower is better)
    double responseTimeScore;
    switch (mappedServiceType) {
      case 'broadband':
        // Broadband/Web: Good <= 200ms, Average <= 500ms, Poor > 500ms
        if (avgResponseTime <= 200) {
          responseTimeScore = 1.0;
        } else if (avgResponseTime <= 500) {
          responseTimeScore = 0.6;
        } else {
          responseTimeScore = 0.3;
        }
        break;
      case 'video':
        // Video: Good <= 100ms, Average <= 300ms, Poor > 300ms
        if (avgResponseTime <= 100) {
          responseTimeScore = 1.0;
        } else if (avgResponseTime <= 300) {
          responseTimeScore = 0.6;
        } else {
          responseTimeScore = 0.3;
        }
        break;
      case 'voice':
        // Voice: Good <= 50ms, Average <= 150ms, Poor > 150ms
        if (avgResponseTime <= 50) {
          responseTimeScore = 1.0;
        } else if (avgResponseTime <= 150) {
          responseTimeScore = 0.6;
        } else {
          responseTimeScore = 0.3;
        }
        break;
      default:
        // Generic service: Good <= 200ms, Average <= 500ms, Poor > 500ms
        if (avgResponseTime <= 200) {
          responseTimeScore = 1.0;
        } else if (avgResponseTime <= 500) {
          responseTimeScore = 0.6;
        } else {
          responseTimeScore = 0.3;
        }
    }
    
    // Calculate error rate score (lower is better)
    double errorRateScore;
    if (errorRate <= 1.0) {
      errorRateScore = 1.0; // Excellent: <= 1% error rate
    } else if (errorRate <= 5.0) {
      errorRateScore = 0.7; // Good: <= 5% error rate
    } else if (errorRate <= 10.0) {
      errorRateScore = 0.4; // Average: <= 10% error rate
    } else {
      errorRateScore = 0.1; // Poor: > 10% error rate
    }
    
    // Calculate volume score (more requests indicate active service)
    double volumeScore = totalRequests > 0 ? 1.0 : 0.5;
    
    // Combine scores: response time (60%), error rate (30%), volume (10%)
    final finalScore = (responseTimeScore * 0.6 + errorRateScore * 0.3 + volumeScore * 0.1).clamp(0.0, 1.0);
    
    print('QoE Widget: Service $serviceType -> $mappedServiceType: Response Score: $responseTimeScore, Error Score: $errorRateScore, Volume Score: $volumeScore, Final QoE: $finalScore');
    
    return finalScore;
  }
  
  double _calculateFallbackQoE(Appstate appState, String serviceType) {
    // Fallback calculation when no service performance data is available
    // Use network metrics as a proxy
    final metricsData = appState.metrics.data;
    
    double totalLatency = 0.0;
    double totalThroughput = 0.0;
    int nodeCount = 0;
    
    for (final nodeMetrics in metricsData.values) {
      if (nodeMetrics.isNotEmpty) {
        final latestMetric = nodeMetrics.last;
        nodeCount++;
        
        // Calculate average throughput and simulate latency
        double nodeThroughput = 0.0;
        int interfaceCount = 0;
        
        latestMetric.interfaces.forEach((interfaceName, interfaceData) {
          if (interfaceData is Map) {
            final receiveThroughput = (interfaceData['byte_recv_throughput'] as num?)?.toDouble() ?? 0.0;
            final sendThroughput = (interfaceData['byte_sent_throughput'] as num?)?.toDouble() ?? 0.0;
            nodeThroughput += (receiveThroughput + sendThroughput) / 1024.0; // KB/s
            interfaceCount++;
          }
        });
        
        if (interfaceCount > 0) {
          totalThroughput += nodeThroughput / interfaceCount;
        }
        
        // Simulate latency based on throughput (inverse relationship)
        totalLatency += math.max(10, 100 - (nodeThroughput / 10));
      }
    }
    
    final avgThroughput = nodeCount > 0 ? totalThroughput / nodeCount : 0.0;
    final avgLatency = nodeCount > 0 ? totalLatency / nodeCount : 50.0;
    
    // Calculate QoE based on service type requirements
    switch (serviceType.toLowerCase()) {
      case 'broadband':
        return _calculateBroadbandFallbackQoE(appState, avgThroughput, avgLatency);
      case 'video':
        return _calculateVideoFallbackQoE(avgThroughput, avgLatency);
      case 'voice':
        return _calculateVoiceFallbackQoE(avgThroughput, avgLatency);
      default:
        return 0.5; // Default moderate score
    }
  }
  
  double _calculateBroadbandFallbackQoE(Appstate appState, double throughput, double latency) {
    // Use average response time from service performance data for web browsing experience
    final avgResponseTime = appState.metrics.averageResponseTime;
    
    // Map average response time to poor/average/good
    double webBrowsingScore;
    if (avgResponseTime <= 200) {
      webBrowsingScore = 1.0;
    } else if (avgResponseTime <= 500) {
      webBrowsingScore = 0.6;
    } else {
      webBrowsingScore = 0.3;
    }
    
    // Combine web browsing experience with traditional throughput and latency metrics
    double throughputScore = math.min(1.0, throughput / 1000.0); // Normalize to 1000 KB/s
    double latencyScore = math.max(0.0, (100 - latency) / 100.0);
    
    // Weight web browsing experience more heavily (50%), with throughput (30%) and latency (20%)
    return (webBrowsingScore * 0.5 + throughputScore * 0.3 + latencyScore * 0.2).clamp(0.0, 1.0);
  }

  double _calculateVideoFallbackQoE(double throughput, double latency) {
    // Video QoE more sensitive to throughput
    double throughputScore = math.min(1.0, throughput / 2000.0); // Higher requirement
    double latencyScore = math.max(0.0, (80 - latency) / 80.0);
    return (throughputScore * 0.8 + latencyScore * 0.2).clamp(0.0, 1.0);
  }

  double _calculateVoiceFallbackQoE(double throughput, double latency) {
    // Voice QoE more sensitive to latency
    double throughputScore = math.min(1.0, throughput / 100.0); // Lower requirement
    double latencyScore = math.max(0.0, (50 - latency) / 50.0);
    return (throughputScore * 0.3 + latencyScore * 0.7).clamp(0.0, 1.0);
  }

  QoELevel _getQoELevel(double value) {
    if (value <= 0.4) return QoELevel.poor;
    if (value <= 0.75) return QoELevel.good;
    return QoELevel.excellent;
  }

  Color _getQoELevelColor(QoELevel level) {
    switch (level) {
      case QoELevel.poor:
        return Colors.red;
      case QoELevel.good:
        return Colors.orange;
      case QoELevel.excellent:
        return Colors.green;
    }
  }

  String _getQoELevelText(QoELevel level) {
    switch (level) {
      case QoELevel.poor:
        return 'POOR';
      case QoELevel.good:
        return 'GOOD';
      case QoELevel.excellent:
        return 'EXCELLENT';
    }
  }
}

class _GaugePainter extends CustomPainter {
  final double value;
  final Color color;
  final Color levelColor;

  _GaugePainter({
    required this.value,
    required this.color,
    required this.levelColor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = math.min(size.width, size.height) / 2 - 8;
    
    // Background arc
    final backgroundPaint = Paint()
      ..color = Colors.grey[200]!
      ..style = PaintingStyle.stroke
      ..strokeWidth = 8
      ..strokeCap = StrokeCap.round;
    
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -math.pi * 0.75, // Start angle
      math.pi * 1.5, // Sweep angle (270 degrees)
      false,
      backgroundPaint,
    );
    
    // Progress arc
    final progressPaint = Paint()
      ..color = levelColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 8
      ..strokeCap = StrokeCap.round;
    
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -math.pi * 0.75, // Start angle
      math.pi * 1.5 * value, // Sweep angle based on value
      false,
      progressPaint,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}
