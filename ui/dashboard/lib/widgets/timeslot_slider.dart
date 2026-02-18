import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'dart:async';
import '../../appstate.dart';
import '../../utils/APIService.dart';
import 'package:intl/intl.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';

enum Timescale {
  oneMinute,
  tenMinutes,
  oneHour,
}

class TimeslotSlider extends StatefulWidget {
  const TimeslotSlider({super.key});

  @override
  State<TimeslotSlider> createState() => _TimeslotSliderState();
}

class _TimeslotSliderState extends State<TimeslotSlider> {
  List<DateTime> _timeWindow = [];
  double _currentIndex = 0;
  Timer? _refreshTimer;
  Timescale _currentScale = Timescale.oneMinute;
  
  // Track if we are currently viewing historical or live
  bool _isLive = true;

  @override
  void initState() {
    super.initState();
    _updateTimeWindow();
    // Refresh the window every second to keep "Live" moving forward
    _refreshTimer = Timer.periodic(const Duration(seconds: 1), (_) {
       if (_isLive && mounted) {
           setState(() {
              _updateTimeWindow();
              // Keep slider thumb pinned to the right if we are live
              _currentIndex = (_timeWindow.length - 1).toDouble();
           });
       } else if (mounted) {
           // Still update the window so the bounds don't get stale,
           // but do not drag the slider thumb forward 
           setState(() {
              _updateTimeWindow();
           });
       }
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  void _updateTimeWindow() {
    final now = DateTime.now();
    final List<DateTime> newWindow = [];
    
    int totalIntervals;
    Duration intervalStep;
    
    switch (_currentScale) {
      case Timescale.oneMinute:
        // 1-minute window in 5-second intervals (12 intervals)
        totalIntervals = 12;
        intervalStep = const Duration(seconds: 5);
        break;
      case Timescale.tenMinutes:
        // 10-minute window in 1-minute intervals (10 intervals)
        totalIntervals = 10;
        intervalStep = const Duration(minutes: 1);
        break;
      case Timescale.oneHour:
        // 1-hour window in 5-minute intervals (12 intervals)
        totalIntervals = 12;
        intervalStep = const Duration(minutes: 5);
        break;
    }

    for (int i = totalIntervals; i >= 0; i--) {
       newWindow.add(now.subtract(intervalStep * i));
    }
    _timeWindow = newWindow;
  }
  
  void _onSliderChanged(double value) {
    setState(() {
      _currentIndex = value;
    });
  }

  void _onSliderChangeEnd(double value) {
    int index = value.toInt();
    if (index < 0 || index >= _timeWindow.length) return;
    
    // If it's the very last index, consider it "Live"
    bool isLatest = index == _timeWindow.length - 1;
    setState(() {
      _isLive = isLatest;
    });
    
    final appState = Provider.of<Appstate>(context, listen: false);
    
    if (isLatest) {
       // Refresh with latest
       appState.fetchAnomalies();
    } else {
       // Fetch historical nearest that selected time
       final selectedTime = _timeWindow[index];
       // Convert to UTC ISO-8601 string for the backend
       appState.fetchAnomalies(timestamp: selectedTime.toUtc().toIso8601String());
    }
  }

  String _formatTimestamp(DateTime dt) {
    try {
      return DateFormat('HH:mm:ss').format(dt.toLocal());
    } catch (_) {
      return dt.toIso8601String();
    }
  }

  String _formatRelativeTime(DateTime dt, bool isLatest) {
    if (isLatest) return 'Live';
    
    final difference = DateTime.now().difference(dt);
    if (difference.inHours > 0) {
       final mins = difference.inMinutes % 60;
       return '-${difference.inHours}h${mins > 0 ? ' ${mins}m' : ''}';
    } else if (difference.inMinutes > 0) {
       final secs = difference.inSeconds % 60;
       return '-${difference.inMinutes}m${secs > 0 ? ' ${secs}s' : ''}';
    } else {
       return '-${difference.inSeconds}s';
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_timeWindow.isEmpty) {
      return const SizedBox.shrink();
    }
    
    final maxIndex = (_timeWindow.length - 1).toDouble();
    final currentTimestamp = _timeWindow[_currentIndex.toInt()];

    return PointerInterceptor(
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onHorizontalDragUpdate: (_) {},
        onVerticalDragUpdate: (_) {},
        child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
        decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.9),
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.1),
            blurRadius: 10,
            spreadRadius: 2,
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
               Row(
                 children: [
                   Text(
                     'Timeline  ', 
                     style: TextStyle(fontWeight: FontWeight.bold, color: Colors.blueGrey.shade700)
                   ),
                   DropdownButton<Timescale>(
                     value: _currentScale,
                     isDense: true,
                     underline: Container(), // Remove default underline
                     icon: const Icon(Icons.arrow_drop_down, size: 20, color: Colors.blueGrey),
                     style: const TextStyle(fontSize: 12, color: Colors.blueGrey, fontWeight: FontWeight.bold),
                     onChanged: (Timescale? newValue) {
                       if (newValue != null && newValue != _currentScale) {
                         setState(() {
                           _currentScale = newValue;
                           _updateTimeWindow();
                           _currentIndex = (_timeWindow.length - 1).toDouble();
                           _isLive = true;
                         });
                         // Trigger live mode fetch immediately when scale changes to avoid stale view
                         Provider.of<Appstate>(context, listen: false).fetchAnomalies();
                       }
                     },
                     items: const [
                       DropdownMenuItem(value: Timescale.oneMinute, child: Text('1 Min (-5s)')),
                       DropdownMenuItem(value: Timescale.tenMinutes, child: Text('10 Min (-1m)')),
                       DropdownMenuItem(value: Timescale.oneHour, child: Text('1 Hr (-5m)')),
                     ],
                   ),
                 ],
               ),
               Row(
                 children: [
                   if (!_isLive) 
                     Container(
                       padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                       margin: const EdgeInsets.only(right: 8),
                       decoration: BoxDecoration(
                         color: Colors.red.shade100,
                         borderRadius: BorderRadius.circular(4),
                       ),
                       child: Text('Historical View', style: TextStyle(color: Colors.red.shade900, fontSize: 12, fontWeight: FontWeight.bold)),
                     ),
                   Text(
                     _isLive ? 'Live' : _formatRelativeTime(currentTimestamp, false),
                     style: TextStyle(
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.bold,
                        color: _isLive ? Colors.green.shade700 : Colors.black87,
                     ),
                   ),
                 ],
               ),
            ],
          ),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
               activeTrackColor: Colors.blue,
               inactiveTrackColor: Colors.blue.withOpacity(0.2),
               trackHeight: 4.0,
               thumbColor: Colors.blue.shade700,
               thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8.0),
               overlayColor: Colors.blue.withOpacity(0.2),
               tickMarkShape: const RoundSliderTickMarkShape(tickMarkRadius: 3.0),
               activeTickMarkColor: Colors.white.withOpacity(0.6),
               inactiveTickMarkColor: Colors.blue.withOpacity(0.4),
               valueIndicatorShape: const RectangularSliderValueIndicatorShape(),
               valueIndicatorColor: Colors.blueGrey.shade800,
               valueIndicatorTextStyle: const TextStyle(color: Colors.white, fontSize: 12),
            ),
            child: Slider(
              value: _currentIndex.clamp(0, maxIndex),
              min: 0,
              max: maxIndex,
              divisions: maxIndex > 0 ? maxIndex.toInt() : null,
              label: _formatRelativeTime(_timeWindow[_currentIndex.toInt()], _currentIndex.toInt() == maxIndex.toInt()),
              onChanged: _onSliderChanged,
              onChangeEnd: _onSliderChangeEnd,
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                _formatRelativeTime(_timeWindow.first, false),
                style: const TextStyle(fontSize: 10, color: Colors.grey),
              ),
              Icon(Icons.arrow_drop_up, size: 12, color: Colors.grey.shade400),
              Text(
                _formatRelativeTime(_timeWindow[_timeWindow.length ~/ 2], false),
                style: const TextStyle(fontSize: 10, color: Colors.grey),
              ),
              Icon(Icons.arrow_drop_up, size: 12, color: Colors.grey.shade400),
              const Text(
                'Live',
                style: TextStyle(fontSize: 10, color: Colors.grey),
              ),
            ],
          )
        ],
      ),
    )));
  }
}
