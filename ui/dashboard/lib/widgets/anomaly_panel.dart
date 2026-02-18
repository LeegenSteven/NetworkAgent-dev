import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';

import '../screens/full_screen_panel_view.dart';
import '../models/panel_type.dart';

class AnomalyPanel extends StatelessWidget {
  final bool isFullScreen;

  const AnomalyPanel({super.key, this.isFullScreen = false});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: BoxDecoration(
            color: Colors.red.shade50,
            borderRadius: const BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Center(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.warning_amber_rounded, size: 18, color: Colors.red.shade700),
                      const SizedBox(width: 8),
                      Text(
                        'Top Anomalies',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: Colors.red.shade900,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              IconButton(
                icon: Icon(
                  isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                  color: Colors.red.shade900,
                ),
                tooltip: isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  if (isFullScreen) {
                    Navigator.of(context).pop();
                  } else {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (context) => const FullScreenPanelView(
                          panelType: PanelType.anomaly,
                        ),
                      ),
                    );
                  }
                },
              ),
            ],
          ),
        ),
          Expanded(
            child: Consumer<Appstate>(
              builder: (context, appState, child) {
                final anomalies = appState.anomalies;
                
                if (anomalies.isEmpty) {
                  return const Center(
                    child: Padding(
                      padding: EdgeInsets.all(16.0),
                      child: Text('No anomalies detected in the current snapshot.', textAlign: TextAlign.center),
                    ),
                  );
                }

                // Filter to only those above threshold
                final activeAnomalies = anomalies.where((a) => (a['anomaly_score'] ?? 0.0) > 0.5).toList();
                
                if (activeAnomalies.isEmpty) {
                   return const Center(
                    child: Padding(
                      padding: EdgeInsets.all(16.0),
                      child: Text('All node scores are below the anomaly threshold.', textAlign: TextAlign.center),
                    ),
                  );
                }

                return ListView.builder(
                  itemCount: activeAnomalies.length,
                  itemBuilder: (context, index) {
                    final anomaly = activeAnomalies[index];
                    final score = anomaly['anomaly_score'] as double? ?? 0.0;
                    
                    return Card(
                      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      elevation: score > 0.8 ? 2 : 0,
                      shape: RoundedRectangleBorder(
                        side: BorderSide(
                          color: score > 0.8 ? Colors.red.shade300 : Colors.red.shade100,
                          width: score > 0.8 ? 2 : 1,
                        ),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: ExpansionTile(
                        leading: CircleAvatar(
                          backgroundColor: score > 0.8 ? Colors.red : Colors.orange,
                          child: Text(
                            score.toStringAsFixed(1),
                            style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                          ),
                        ),
                        title: Text(
                          anomaly['name'] ?? 'Unknown Node',
                          style: const TextStyle(fontWeight: FontWeight.bold),
                        ),
                        subtitle: Text(anomaly['node_type'] ?? ''),
                        children: [
                          Padding(
                            padding: const EdgeInsets.all(16.0),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text('Root Cause Analysis:', style: TextStyle(fontWeight: FontWeight.bold)),
                                const SizedBox(height: 8),
                                Text(
                                  anomaly['root_cause']?.toString() ?? 'No explanation available.',
                                  style: Theme.of(context).textTheme.bodyMedium,
                                ),
                                const SizedBox(height: 16),
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: TextButton.icon(
                                    onPressed: () {
                                      appState.getNodeDetails(anomaly['node_id']);
                                    },
                                    icon: const Icon(Icons.info_outline, size: 16),
                                    label: const Text('View Node Details'),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
    );
  }
}
