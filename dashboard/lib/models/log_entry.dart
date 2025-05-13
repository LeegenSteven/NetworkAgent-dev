class LogEntry {
  final String timestamp;
  final String level;
  final String message;
  final String source;
  final Map<String, dynamic> details;

  LogEntry({
    required this.timestamp,
    required this.level,
    required this.message,
    required this.source,
    this.details = const {},
  });

  factory LogEntry.fromJson(Map<String, dynamic> json) {
    return LogEntry(
      timestamp: json['timestamp'] ?? DateTime.now().toIso8601String(),
      level: json['level'] ?? 'INFO',
      message: json['message'] ?? '',
      source: json['source'] ?? 'unknown',
      details: json['details'] ?? {},
    );
  }
}
