// generat a dart class called Metrics entry that receives
// a JSON like the one below and turns it into dart object
// with attribute to access it
//
// Example of a MetricEntry
// {
//   "hostname": "ljulliard1",
//   "interval": 5,
//   "cpu": {
//     "cpu_percent": 6.2
//   },
//   "interfaces": {
//     "wlp0s20f3": {
//       "byte_sent": 2023948021,
//       "byte_sent_delta": 0,
//       "byte_recv": 11340335143,
//       "byte_recv_delta": 0,
//       "byte_sent_throughput": 0.0,
//       "byte_recv_throughput": 0.0
//     },
//     "enx00e04c6845c8": {
//       "byte_sent": 3107597262,
//       "byte_sent_delta": 91950,
//       "byte_recv": 8613681786,
//       "byte_recv_delta": 79914,
//       "byte_sent_throughput": 18390.0,
//       "byte_recv_throughput": 15982.8
//     }
//   },
//   "timestamp": 1745436168
// }
class MetricEntry {
  final String hostname;
  final int interval;
  final Map<String, dynamic> cpu;
  final Map<String, dynamic> interfaces;
  final int timestamp;

  MetricEntry({
    required this.hostname,
    required this.interval,
    required this.cpu,
    required this.interfaces,
    required this.timestamp,
  });

  factory MetricEntry.fromJson(Map<String, dynamic> json) {
    return MetricEntry(
      hostname: json['hostname'] ?? '',
      interval: json['interval'] ?? 0,
      cpu: json['cpu'] ?? {},
      interfaces: json['interfaces'] ?? {},
      timestamp: json['timestamp'] ?? 0,
    );
  }
}

