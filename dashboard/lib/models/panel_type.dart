enum PanelType {
  chat,
  logs,
  performance,
}

extension PanelTypeExtension on PanelType {
  String get displayName {
    switch (this) {
      case PanelType.chat:
        return 'Network Agent Chat';
      case PanelType.logs:
        return 'System Logs';
      case PanelType.performance:
        return 'Performance Graphs';
    }
  }
}
