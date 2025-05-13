import 'package:flutter/material.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'models/chat_message.dart';

class Appstate extends ChangeNotifier {
  // Chat state
  final List<ChatMessage> _chatMessages = [];
  io.Socket? _socket;
  
  // Getters
  List<ChatMessage> get chatMessages => _chatMessages;
  io.Socket? get socket => _socket;
  
  Appstate();
  
  // Set the socket connection
  void setSocket(io.Socket socket) {
    _socket = socket;
    
    // Listen for chat messages from the server
    _socket!.on('chat_message', (data) {
      if (data != null) {
        final message = ChatMessage(
          id: data['id'] ?? DateTime.now().millisecondsSinceEpoch.toString(),
          text: data['text'] ?? '',
          isUser: data['isUser'] ?? false,
          timestamp: data['timestamp'] != null 
              ? DateTime.parse(data['timestamp']) 
              : DateTime.now(),
        );
        addChatMessage(message);
      }
    });
  }
  
  // Add a chat message
  void addChatMessage(ChatMessage message) {
    _chatMessages.add(message);
    notifyListeners();
  }
  
  // Add a new chat message with text
  void addChatMessageWithText(String text, bool isUser) {
    final message = ChatMessage(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      text: text,
      isUser: isUser,
      timestamp: DateTime.now(),
    );
    addChatMessage(message);
  }
  
  // Send a message to the server
  Future<void> sendChatMessage(String text) async {
    if (text.trim().isEmpty) return;
    
    // Add user message to local state
    addChatMessageWithText(text, true);
    
    // Send message to server if socket is connected
    if (_socket != null && _socket!.connected) {
      _socket!.emit('chat_message', {'text': text});
    }
  }
  
  // Reset chat history
  void resetChat() {
    _chatMessages.clear();
    
    // Send reset_chat event to server if socket is connected
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_chat', {});
    }
    
    notifyListeners();
  }
  
  @override
  void dispose() {
    // Remove event listeners to prevent memory leaks
    if (_socket != null) {
      _socket!.off('chat_message');
    }
    super.dispose();
  }
}
