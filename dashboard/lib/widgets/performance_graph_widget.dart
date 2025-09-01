import 'package:flutter/material.dart';
import 'active_users_counter_widget.dart';
import 'qoe_gauges_widget.dart';
import 'node_performance_widget.dart';

class PerformanceGraphWidget extends StatefulWidget {
  final socket;
  final bool isLoading;

  const PerformanceGraphWidget({
    super.key,
    required this.socket,
    this.isLoading = false,
  });

  @override
  State<PerformanceGraphWidget> createState() => _PerformanceGraphWidgetState();
}

class _PerformanceGraphWidgetState extends State<PerformanceGraphWidget> {
  @override
  Widget build(BuildContext context) {
    if (widget.isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    return SingleChildScrollView(
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
    );
  }
}
