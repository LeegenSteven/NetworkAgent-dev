import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/agent.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Google logo
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.asset(
                'assets/images/google.png',
                width: 24,
                height: 24,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(width: 12),
            const Text(
              'Settings',
              style: TextStyle(
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        backgroundColor: const Color(0xFF0D47A1),
        foregroundColor: Colors.white,
        centerTitle: true,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              // Implementation left empty for now
            },
            tooltip: 'Refresh',
          ),
        ],
      ),
      body: const Padding(
        padding: EdgeInsets.all(16.0),
        child: AgentSettingsSection(),
      ),
    );
  }
}

class AgentSettingsSection extends StatelessWidget {
  const AgentSettingsSection({super.key});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Agents',
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: const Color(0xFF0D47A1),
                      ),
                ),
                ElevatedButton.icon(
                  onPressed: () => _showAddAgentDialog(context),
                  icon: const Icon(Icons.add),
                  label: const Text('Add Agent'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0D47A1),
                    foregroundColor: Colors.white,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            const Text(
              'Configure network agents with their description and URL.',
              style: TextStyle(
                color: Colors.grey,
                fontSize: 14,
              ),
            ),
            const SizedBox(height: 16),
            const Expanded(
              child: AgentList(),
            ),
          ],
        );
      }
    );
  }

  void _showAddAgentDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => const AddAgentDialog(),
    );
  }
}

class AgentList extends StatelessWidget {
  const AgentList({super.key});

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);
    final agents = appState.agents;

    if (agents.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.engineering,
              size: 64,
              color: Colors.grey[400],
            ),
            const SizedBox(height: 16),
            Text(
              'No agents configured',
              style: TextStyle(
                fontSize: 18,
                color: Colors.grey[600],
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Add an agent to get started',
              style: TextStyle(
                fontSize: 14,
                color: Colors.grey[500],
              ),
            ),
          ],
        ),
      );
    }

    return ListView.builder(
      itemCount: agents.length,
      itemBuilder: (context, index) {
        final agent = agents[index];
        return AgentListItem(agent: agent);
      },
    );
  }
}

class AgentListItem extends StatelessWidget {
  final Agent agent;

  const AgentListItem({super.key, required this.agent});

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context, listen: false);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Google logo image on the left side
            Image.asset(
              agent.name == "Anomaly Resolution Agent" ? 'assets/images/Zinkworks.png' : 'assets/images/google.png',
              width: 80,
              height: 40,
              fit: BoxFit.contain,
            ),
            const SizedBox(width: 16), // Spacing between image and content
            // Main content column
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Text(
                          agent.name.isNotEmpty ? agent.name : 'Unnamed Agent',
                          style: const TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                          ),
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.delete_outline, color: Colors.red),
                        onPressed: () => _confirmDelete(context, appState),
                        tooltip: 'Remove Agent',
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Description: ${agent.description}',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontSize: 14,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'URL: ${agent.url}',
                    style: TextStyle(
                      color: Colors.grey[700],
                      fontSize: 14,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _confirmDelete(BuildContext context, Appstate appState) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove Agent'),
        content: Text('Are you sure you want to remove "${agent.name.isNotEmpty ? agent.name : 'Unnamed Agent'}"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              await appState.removeAgent(agent.id);
              if (context.mounted) {
                Navigator.of(context).pop();
              }
            },
            child: const Text('Remove', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }
}

class AddAgentDialog extends StatefulWidget {
  const AddAgentDialog({super.key});

  @override
  State<AddAgentDialog> createState() => _AddAgentDialogState();
}

class _AddAgentDialogState extends State<AddAgentDialog> {
  final _urlController = TextEditingController();
  final _focusNode = FocusNode();
  
  @override
  void initState() {
    super.initState();
    // Request focus after the dialog is built
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focusNode.requestFocus();
    });
  }
  
  @override
  void dispose() {
    _urlController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Add Agent URL'),
      content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
             TextField(
              controller: _urlController,
              focusNode: _focusNode,
              decoration: const InputDecoration(
                hintText: 'Enter agent URL',
                border: OutlineInputBorder(),
                contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              ),
              autofocus: true,
              onSubmitted: (_) => _addAgent(),
            ),
          ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: _addAgent,
          child: const Text('Add'),
        ),
      ],
    );
  }

  Future<void> _addAgent() async {
    final appState = Provider.of<Appstate>(context, listen: false);
    await appState.addAgent(
      _urlController.text.trim(),
    );
    if (context.mounted) {
      Navigator.of(context).pop();
    }
  }
}
