import 'package:flutter/material.dart';
import '../../models/panel_type.dart';
import '../../screens/full_screen_panel_view.dart';
import 'active_users_counter_widget.dart';
import 'qoe_gauges_widget.dart';
import 'node_performance_widget.dart';

class PerformanceGraphWidget extends StatefulWidget {
  final socket;
  final bool isLoading;
  final bool isFullScreen;

  const PerformanceGraphWidget({
    super.key,
    required this.socket,
    this.isLoading = false,
    this.isFullScreen = false,
  });

  @override
  State<PerformanceGraphWidget> createState() => _PerformanceGraphWidgetState();
}

class _PerformanceGraphWidgetState extends State<PerformanceGraphWidget> {
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Header with title and expand button
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Centered Title
              Center(
                child: Text(
                  'Performance Graphs',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0D47A1), // Dark blue text
                  ),
                ),
              ),
              
              // Expand/Collapse button (positioned on the right)
              Positioned(
                right: 0,
                child: IconButton(
                  icon: Icon(
                    widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen, 
                    color: Color(0xFF0D47A1)
                  ),
                  tooltip: widget.isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                  onPressed: () {
                    if (widget.isFullScreen) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (context) => FullScreenPanelView(
                            panelType: PanelType.performance,
                            socket: widget.socket,
                            isLoading: widget.isLoading,
                          ),
                        ),
                      );
                    }
                  },
                ),
              ),
            ],
          ),
        ),
        
        // Performance graphs content
        Expanded(
          child: widget.isLoading
              ? const Center(child: CircularProgressIndicator())
              : SingleChildScrollView(
                  padding: const EdgeInsets.all(8.0),
                  child: Column(
                    children: [
                      // Top row with Active Users Counter and QoE Gauges
                      IntrinsicHeight(
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            // Active Users Counter (left side)
                            Expanded(
                              flex: 1,
                              child: ActiveUsersCounterWidget(),
                            ),
                            
                            // QoE Gauges (right side, takes more space)
                            Expanded(
                              flex: 2,
                              child: QoEGaugesWidget(),
                            ),
                          ],
                        ),
                      ),
                      
                      // Bottom section with Node Performance Graph
                      // Use a fixed height container instead of Expanded since we're in a scroll view
                      SizedBox(
                        height: 400, // Fixed height for the performance graph
                        child: NodePerformanceWidget(),
                      ),
                    ],
                  ),
                ),
        ),
      ],
    );
  }
}
