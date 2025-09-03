import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../models/chat_message.dart';
import '../appstate.dart';

class ChatProvider extends ChangeNotifier {
  final TextEditingController messageController = TextEditingController();
  bool _isLoading = false;
  // Callback for when a message is received from the server
  VoidCallback? onMessageReceived;

  bool get isLoading => _isLoading;

  Future<void> sendMessage(String text, Appstate appState) async {
    if (text.trim().isEmpty) return;

    messageController.clear();

    // Show loading indicator
    _isLoading = true;
    notifyListeners();

    // Use the AppState to send the message
    await appState.sendChatMessage(text);
    
    // Note: We no longer hide the loading indicator here
    // It will be hidden when a response is received from the server
    
    // Trigger the focus callback after sending a message
    if (onMessageReceived != null) {
      // Use a delay to ensure UI has updated
      Future.delayed(const Duration(milliseconds: 300), () {
        onMessageReceived!();
      });
    }
  }
  
  // Method to hide loading indicator when a response is received
  void messageReceived() {
    if (_isLoading) {
      _isLoading = false;
      notifyListeners();
    }
  }

  void resetChat(Appstate appState) {
    // Hide the loading indicator if it's showing
    if (_isLoading) {
      _isLoading = false;
      notifyListeners();
    }
    
    appState.resetChat();
  }
}

class ChatPanel extends StatelessWidget {
  final io.Socket socket;
  
  const ChatPanel({
    super.key,
    required this.socket,
  });

  @override
  Widget build(BuildContext context) {
    // Get the AppState
    final appState = Provider.of<Appstate>(context, listen: false);
    
    
    return ChangeNotifierProvider(
      create: (context) {
        return ChatProvider();
      },
      child: const _ChatPanelContent(),
    );
  }
}

class _ChatPanelContent extends StatefulWidget {
  const _ChatPanelContent();

  @override
  State<_ChatPanelContent> createState() => _ChatPanelContentState();
}

class _ChatPanelContentState extends State<_ChatPanelContent> with WidgetsBindingObserver {
  // ScrollController to manage auto-scrolling
  final ScrollController _scrollController = ScrollController();
  // FocusNode to manage text field focus
  final FocusNode _textFieldFocus = FocusNode();
  // Global key for the text field
  final GlobalKey<EditableTextState> _textFieldKey = GlobalKey<EditableTextState>();
  late ChatProvider _chatProvider;
  bool _isInitialized = false;
  
  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _scrollController.dispose();
    _textFieldFocus.dispose();
    // Remove the callback to prevent memory leaks
    _chatProvider.onMessageReceived = null;
    super.dispose();
  }
  
  // Method to scroll to the bottom of the chat
  void _scrollToBottom() {
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    }
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    
    // Add listener to focus node to detect when focus is lost
    _textFieldFocus.addListener(_onFocusChange);
    
    // Request focus when the widget is first created
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _forceFocus();
      _isInitialized = true;
      
      // Initialize the last message count
      final appState = Provider.of<Appstate>(context, listen: false);
      _lastMessageCount = appState.chatMessages.length;
    });
  }
  
  // Called when app lifecycle changes (e.g., app comes to foreground)
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // App came to foreground, request focus
      _forceFocus();
    }
  }
  
  void _onFocusChange() {
    // If focus is lost and the widget is initialized and visible, try to regain it
    // We only want to force focus when the chat panel is actively being used
    if (!_textFieldFocus.hasFocus && _isInitialized && mounted && context.findRenderObject() != null) {
      // Check if we're currently in a dialog by looking at the ModalRoute
      final isInDialog = ModalRoute.of(context)?.isCurrent != true;
      
      // Only force focus if we're not in a dialog
      if (!isInDialog) {
        Future.delayed(const Duration(milliseconds: 100), _forceFocus);
      }
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Get the provider and set up the callback
    _chatProvider = Provider.of<ChatProvider>(context);
    _chatProvider.onMessageReceived = _refocusTextField;
    
    // Listen for changes in the AppState's chat messages
    final appState = Provider.of<Appstate>(context, listen: false);
    // Update _lastMessageCount when chat is reset
    if (appState.chatMessages.isEmpty) {
      _lastMessageCount = 0;
    }
  }
  
  // Method to force focus on the text field
  void _forceFocus() {
    // Only proceed if the widget is still mounted and visible
    if (!mounted || context.findRenderObject() == null) return;
    
    // Check if we're currently in a dialog by looking at the ModalRoute
    final isInDialog = ModalRoute.of(context)?.isCurrent != true;
    
    // Don't force focus if we're in a dialog
    if (isInDialog) return;
    
    // Use FocusScope to ensure focus is properly managed
    FocusScope.of(context).unfocus();
    
    // Try to focus using the FocusNode
    if (_textFieldFocus.canRequestFocus) {
      _textFieldFocus.requestFocus();
    }
    
    // Try to focus using the GlobalKey as a fallback
    if (_textFieldKey.currentState != null) {
      _textFieldKey.currentState!.requestKeyboard();
    }
  }
  
  // Method to explicitly refocus the text field
  void _refocusTextField() {
    // Only proceed if the widget is still mounted and visible
    if (!mounted || context.findRenderObject() == null) return;
    
    // Check if we're currently in a dialog by looking at the ModalRoute
    final isInDialog = ModalRoute.of(context)?.isCurrent != true;
    
    // Don't force focus if we're in a dialog
    if (isInDialog) return;
    
    // Force focus immediately
    _forceFocus();
    
    // And also try with delays to ensure it works, but only if not in a dialog
    Future.delayed(const Duration(milliseconds: 200), _forceFocus);
    Future.delayed(const Duration(milliseconds: 500), _forceFocus);
    Future.delayed(const Duration(milliseconds: 1000), _forceFocus);
  }

  // Keep track of the last message count to detect new messages
  int _lastMessageCount = 0;
  
  @override
  Widget build(BuildContext context) {
    final chatProvider = _chatProvider;
    final appState = Provider.of<Appstate>(context);
    
    // Listen for changes in the message list and scroll to bottom when new messages arrive
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (appState.chatMessages.isNotEmpty) {
        
        // Check if a new message has been added
        if (appState.chatMessages.length > _lastMessageCount) {
          _scrollToBottom();
          
          // Get the latest message
          final latestMessage = appState.chatMessages.last;
          
          // If it's a server message (not from the user), hide the loading indicator
          if (!latestMessage.isUser) {
            chatProvider.messageReceived();
          }
          
          // Update the last message count
          _lastMessageCount = appState.chatMessages.length;
        }
      }
    });
    
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40, // Reduced height from 56 to 40
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0), // Reduced vertical padding
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              // Dropdown menu button on the left
              PopupMenuButton<String>(
                icon: const Icon(Icons.menu, color: Color(0xFF0D47A1)),
                tooltip: 'Quick questions',
                onSelected: (String value) {
                  // Set the selected text to the message controller
                  chatProvider.messageController.text = value;
                  // Request focus on the text field
                  _forceFocus();
                },
                itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
                  const PopupMenuItem<String>(
                    value: 'What network services can i deploy?',
                    child: Text('What network services can i deploy?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Give me more detail about the UERanSIM network service',
                    child: Text('Give me more detail about the UERanSIM network service'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What network services are already deployed?',
                    child: Text('What network services are already deployed?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What is the status of the control plane network service?',
                    child: Text('What is the status of the control plane network service?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What locations are there?',
                    child: Text('What locations are there?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Propose a plan to deploy a 5G core',
                    child: Text('Propose a plan to deploy a 5G core'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a plan to deploy a new network location called cellsite1 with CIDR  10.0.40.0/24',
                    child: Text('Create a plan to deploy a new network location called cellsite1 with CIDR 10.0.40.0/24'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Can you add a radio simulator to cellsite1 and create a plan for a working 5G network',
                    child: Text('Can you add a radio simulator to cellsite1 and create a plan for a working 5G network'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a test called test1 between cellsite1-ueransim and DNN dnn',
                    child: Text('Create a test called test1 between cellsite1-ueransim and DNN dnn'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a plan to delete the ueransim network service, the ptp network connectivity service and the cellsite1 network location',
                    child: Text('Create a plan to delete the ueransim network service, the ptp network connectivity service and the cellsite1 network location'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Were there any error logs in the last 2 hours?',
                    child: Text('Were there any error logs in the last 2 hours?'),
                  ),
                ],
              ),
              // Center title
              Expanded(
                child: Center(
                  child: Text(
                    'Network Agent Chat',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith( // Changed from titleLarge to titleMedium
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0D47A1), // Dark blue text
                    ),
                  ),
                ),
              ),
              // Reset button on the right
              IconButton(
                icon: const Icon(Icons.delete_forever, color: Color(0xFF0D47A1)),
                tooltip: 'Reset chat',
                onPressed: () {
                  chatProvider.resetChat(appState);
                },
              ),
            ],
          ),
        ),
        // Divider removed as requested
        Expanded(
          child: appState.chatMessages.isEmpty
              ? const Center(
                  child: Text('No messages yet. Start a conversation!'),
                )
              : ListView.builder(
                  controller: _scrollController,
                  padding: const EdgeInsets.all(8.0),
                  itemCount: appState.chatMessages.length,
                  itemBuilder: (context, index) {
                    final message = appState.chatMessages[index];
                    return _ChatMessageWidget(message: message);
                  },
                ),
        ),
        const Divider(),
        if (chatProvider.isLoading)
          const Padding(
            padding: EdgeInsets.all(8.0),
            child: LinearProgressIndicator(),
          ),
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  key: _textFieldKey,
                  controller: chatProvider.messageController,
                  focusNode: _textFieldFocus,
                  autofocus: true,
                  maxLines: null, // Allow unlimited lines
                  minLines: 1, // Start with one line
                  textInputAction: TextInputAction.send, // Use send for Enter key
                  keyboardType: TextInputType.multiline, // Enable multiline keyboard
                  decoration: const InputDecoration(
                    hintText: 'Type a message...',
                    border: OutlineInputBorder(),
                    contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  onSubmitted: (text) {
                    // Send message when Enter is pressed
                    chatProvider.sendMessage(text, appState);
                    // Request focus again after submitting
                    _forceFocus();
                  },
                  onTap: () {
                    // Ensure the text field keeps focus when tapped
                    _forceFocus();
                  },
                  onChanged: (text) {
                    // This is just to ensure the text field updates properly
                    setState(() {});
                  },
                ),
              ),
              const SizedBox(width: 8.0),
              IconButton(
                icon: const Icon(Icons.send),
                onPressed: () {
                  chatProvider.sendMessage(
                    chatProvider.messageController.text,
                    appState
                  );
                  // Request focus again after sending message with the button
                  _forceFocus();
                },
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _ChatMessageWidget extends StatelessWidget {
  final ChatMessage message;

  const _ChatMessageWidget({required this.message});

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: message.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4.0),
        padding: const EdgeInsets.all(12.0),
        decoration: BoxDecoration(
          color: message.isUser
              ? const Color(0xFF0D47A1) // Dark blue for user messages
              : const Color(0xFFE3F2FD), // Light blue background for agent messages
          borderRadius: BorderRadius.circular(12.0),
        ),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.7,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Use Markdown widget to render the message text
            MarkdownBody(
              data: message.text,
              selectable: true, // Allow text selection
              styleSheet: MarkdownStyleSheet(
                p: TextStyle(
                  color: message.isUser ? Colors.white : const Color(0xFF0D47A1),
                ),
                h1: TextStyle(
                  color: message.isUser ? Colors.white : const Color(0xFF0D47A1),
                  fontWeight: FontWeight.bold,
                ),
                h2: TextStyle(
                  color: message.isUser ? Colors.white : const Color(0xFF0D47A1),
                  fontWeight: FontWeight.bold,
                ),
                h3: TextStyle(
                  color: message.isUser ? Colors.white : const Color(0xFF0D47A1),
                  fontWeight: FontWeight.bold,
                ),
                code: TextStyle(
                  color: message.isUser ? Colors.white70 : const Color(0xFF01579B),
                  backgroundColor: message.isUser 
                      ? Colors.white.withOpacity(0.15) 
                      : const Color(0xFFE1F5FE),
                ),
                codeblockDecoration: BoxDecoration(
                  color: message.isUser 
                      ? Colors.white.withOpacity(0.15) 
                      : const Color(0xFFE1F5FE),
                  borderRadius: BorderRadius.circular(4.0),
                ),
                blockquote: TextStyle(
                  color: message.isUser ? Colors.white70 : const Color(0xFF0D47A1).withOpacity(0.7),
                  fontStyle: FontStyle.italic,
                ),
                listBullet: TextStyle(
                  color: message.isUser ? Colors.white : const Color(0xFF0D47A1),
                ),
              ),
            ),
            const SizedBox(height: 4.0),
            Text(
              '${message.timestamp.hour}:${message.timestamp.minute.toString().padLeft(2, '0')}',
              style: TextStyle(
                fontSize: 10.0,
                color: message.isUser
                    ? Colors.white.withOpacity(0.7)
                    : const Color(0xFF0D47A1).withOpacity(0.7),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
