import 'package:flutter/foundation.dart';

/// Represents a push notification from the supervisor agent
class PushNotification {
  /// Unique identifier for the notification
  final String id;
  
  /// Name of the notification
  final String name;
  
  /// State of the notification (e.g., 'input_required', 'completed', etc.)
  final String state;
  
  /// Task ID associated with the notification (optional)
  final String? taskId;
  
  /// Context ID associated with the notification (optional)
  final String? contextId;
  
  /// Content/message of the notification
  final String content;
  
  /// Timestamp when the notification was received
  final DateTime timestamp;
  
  /// Whether the notification has been read by the user
  final bool isRead;

  /// Creates a new push notification
  const PushNotification({
    required this.id,
    required this.name,
    required this.state,
    this.taskId,
    this.contextId,
    required this.content,
    required this.timestamp,
    this.isRead = false,
  });

  /// Creates a copy of this notification with the given fields replaced with new values
  PushNotification copyWith({
    String? id,
    String? name,
    String? state,
    String? taskId,
    String? contextId,
    String? content,
    DateTime? timestamp,
    bool? isRead,
  }) {
    return PushNotification(
      id: id ?? this.id,
      name: name ?? this.name,
      state: state ?? this.state,
      taskId: taskId ?? this.taskId,
      contextId: contextId ?? this.contextId,
      content: content ?? this.content,
      timestamp: timestamp ?? this.timestamp,
      isRead: isRead ?? this.isRead,
    );
  }

  /// Creates a notification from a JSON object
  factory PushNotification.fromJson(Map<String, dynamic> json) {
    return PushNotification(
      id: json['id'] ?? DateTime.now().millisecondsSinceEpoch.toString(),
      name: json['name'] ?? 'Notification',
      state: json['state'] ?? 'unknown',
      taskId: json['task_id'],
      contextId: json['context_id'],
      content: json['content'] ?? '',
      timestamp: json['timestamp'] != null 
          ? DateTime.parse(json['timestamp']) 
          : DateTime.now(),
      isRead: json['isRead'] ?? false,
    );
  }

  /// Converts this notification to a JSON object
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'state': state,
      'task_id': taskId,
      'context_id': contextId,
      'content': content,
      'timestamp': timestamp.toIso8601String(),
      'isRead': isRead,
    };
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is PushNotification &&
        other.id == id &&
        other.name == name &&
        other.state == state &&
        other.taskId == taskId &&
        other.contextId == contextId &&
        other.content == content &&
        other.timestamp == timestamp &&
        other.isRead == isRead;
  }

  @override
  int get hashCode {
    return Object.hash(
      id,
      name,
      state,
      taskId,
      contextId,
      content,
      timestamp,
      isRead,
    );
  }

  @override
  String toString() {
    return 'PushNotification(id: $id, name: $name, state: $state, taskId: $taskId, contextId: $contextId, content: $content, timestamp: $timestamp, isRead: $isRead)';
  }
}
