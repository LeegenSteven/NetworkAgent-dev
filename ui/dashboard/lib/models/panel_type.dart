enum PanelType {
  chat,
  logs,
  performance,
  trace,
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
      case PanelType.trace:
        return 'Agent Traces';
    }
  }
}
