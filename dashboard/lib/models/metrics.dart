import 'metric_entry.dart';

/// A simple class to store metrics data for network nodes.
// Example of a Metrics with 2 uid entries (K8s resource uid as key)
// {
//   "eac8bfd1-f7f5-4a23-98ee-791eaa0d1c80": [
//     List of MetricEntry here (last or all)
//   ],
//   "fe07d625-327c-4b07-bbaf-d635c4c5fee2": [
//     List of MetricEntry here (last or all)
//   ]
// }


class Metrics {
  /// The metrics data, keyed by node ID.
  final Map<String, List<MetricEntry>> data;

  /// Creates a new Metrics instance with the provided data.
  Metrics(this.data);

  /// Creates a new Metrics instance from the provided metrics data.
  factory Metrics.fromJson(dynamic metricsData) {
    Map<String, List<MetricEntry>> parsedData = {};
    
    if (metricsData is Map) {
      metricsData.forEach((id, metricsList) {
        if (metricsList is List) {
          parsedData[id.toString()] = metricsList.map((metricItem) {
            if (metricItem is Map && metricItem.containsKey('metrics')) {
              final metricsData = metricItem['metrics'];
              if (metricsData is Map) {
                final typedMetricsData = Map<String, dynamic>.from(metricsData);
                typedMetricsData['timestamp'] = metricItem['timestamp'] ?? 0;
                return MetricEntry.fromJson(typedMetricsData);
              }
            }
            return MetricEntry(
              hostname: '', 
              interval: 0, 
              cpu: {}, 
              interfaces: {}, 
              timestamp: metricItem is Map ? metricItem['timestamp'] ?? 0 : 0
            );
          }).toList();
        } else {
          parsedData[id.toString()] = [
            MetricEntry(hostname: '', interval: 0, cpu: {}, interfaces: {}, timestamp: 0)
          ];
        }
      });
    }
    
    return Metrics(parsedData);
  }
}
