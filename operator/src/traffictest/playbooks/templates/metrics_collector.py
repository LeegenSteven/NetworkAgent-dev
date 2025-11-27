#!/usr/bin/env python3
"""
Metrics Collector for TrafficTest resource.
Collects metrics from iperf3 results and sends them to InfluxDB.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import sys

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
except ImportError:
    print("influxdb-client not available. Install with: pip install influxdb-client")
    sys.exit(1)

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Collects and exports traffic metrics to InfluxDB"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.test_name = config.get('test_name', 'unknown')
        self.source_cpe = config['source_cpe']
        self.destination_cpe = config['destination_cpe']
        self.protocol = config['protocol']
        self.pattern_type = config.get('pattern_type', 'constant')
        
        # InfluxDB configuration - templated from Ansible variables
        self.influxdb_url = "{{ influxdb_url }}"
        self.influxdb_token = "{{ influxdb_token }}"
        self.influxdb_org = "{{ influxdb_org }}"
        self.influxdb_bucket = config.get('influxdb_bucket', "{{ influxdb_bucket | default('telegraf') }}")
        
        # Validate required configuration
        if not self.influxdb_url:
            raise ValueError("InfluxDB URL is required")
        if not self.influxdb_token:
            raise ValueError("InfluxDB token is required")
        if not self.influxdb_org:
            raise ValueError("InfluxDB organization is required")
        
        self.client = None
        self.write_api = None
        
    def _connect_influxdb(self):
        """Connect to InfluxDB"""
        try:
            self.client = InfluxDBClient(
                url=self.influxdb_url,
                token=self.influxdb_token,
                org=self.influxdb_org
            )
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            
            # Test connection
            health = self.client.health()
            if health.status == "pass":
                logger.info(f"Connected to InfluxDB at {self.influxdb_url}")
                return True
            else:
                logger.error(f"InfluxDB health check failed: {health}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            return False
    
    def _parse_iperf3_result(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse iperf3 JSON result and extract metrics"""
        try:
            if 'end' not in result:
                logger.warning("No 'end' section in iperf3 result")
                return None
            
            end_data = result['end']
            
            # Extract summary metrics
            sum_sent = end_data.get('sum_sent', {})
            sum_received = end_data.get('sum_received', {})
            
            metrics = {
                'timestamp': datetime.now(timezone.utc),
                'test_duration': end_data.get('seconds', 0),
            }
            
            # Throughput metrics
            if 'bits_per_second' in sum_sent:
                metrics['throughput_sent_bps'] = sum_sent['bits_per_second']
            if 'bits_per_second' in sum_received:
                metrics['throughput_received_bps'] = sum_received['bits_per_second']
            
            # Packet metrics
            if 'packets' in sum_sent:
                metrics['packets_sent'] = sum_sent['packets']
            if 'packets' in sum_received:
                metrics['packets_received'] = sum_received['packets']
            
            # Loss metrics
            if 'lost_packets' in sum_sent:
                metrics['lost_packets'] = sum_sent['lost_packets']
            if 'lost_percent' in sum_sent:
                metrics['packet_loss_pct'] = sum_sent['lost_percent']
            
            # Retransmission metrics (TCP only)
            if 'retransmits' in sum_sent:
                metrics['retransmissions'] = sum_sent['retransmits']
            
            # Jitter metrics (UDP only)
            if 'jitter_ms' in sum_received:
                metrics['jitter_ms'] = sum_received['jitter_ms']
            
            # Bandwidth metrics
            if 'bytes' in sum_sent and 'seconds' in end_data and end_data['seconds'] > 0:
                metrics['avg_bandwidth_bps'] = (sum_sent['bytes'] * 8) / end_data['seconds']
            
            # CPU utilization
            cpu_utilization_percent = end_data.get('cpu_utilization_percent', {})
            if 'host_total' in cpu_utilization_percent:
                metrics['cpu_utilization_pct'] = cpu_utilization_percent['host_total']
            
            # Connection count (estimate from streams)
            if 'streams' in result:
                metrics['active_connections'] = len(result['streams'])
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error parsing iperf3 result: {e}")
            return None
    
    def _create_influx_point(self, metrics: Dict[str, Any]) -> Point:
        """Create InfluxDB point from metrics"""
        point = Point("traffic_test") \
            .tag("test_name", self.test_name) \
            .tag("source_cpe", self.source_cpe) \
            .tag("destination_cpe", self.destination_cpe) \
            .tag("protocol", self.protocol) \
            .tag("pattern_type", self.pattern_type) \
            .time(metrics['timestamp'], WritePrecision.NS)
        
        # Add numeric fields
        numeric_fields = [
            'throughput_sent_bps', 'throughput_received_bps',
            'packets_sent', 'packets_received', 'lost_packets',
            'packet_loss_pct', 'retransmissions', 'jitter_ms',
            'avg_bandwidth_bps', 'cpu_utilization_pct',
            'active_connections', 'test_duration'
        ]
        
        for field in numeric_fields:
            if field in metrics and metrics[field] is not None:
                point = point.field(field, float(metrics[field]))
        
        return point
    
    def write_metrics(self, iperf3_results: List[Dict[str, Any]]) -> bool:
        """Write metrics from iperf3 results to InfluxDB"""
        if not self.client and not self._connect_influxdb():
            logger.error("Cannot write metrics: InfluxDB connection failed")
            return False
        
        if not iperf3_results:
            logger.warning("No iperf3 results to write")
            return True
        
        points = []
        
        for result in iperf3_results:
            metrics = self._parse_iperf3_result(result)
            if metrics:
                point = self._create_influx_point(metrics)
                points.append(point)
        
        if not points:
            logger.warning("No valid metrics to write")
            return True
        
        try:
            self.write_api.write(bucket=self.influxdb_bucket, record=points)
            logger.info(f"Successfully wrote {len(points)} metric points to InfluxDB")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write metrics to InfluxDB: {e}")
            return False
    
    def write_realtime_metrics(self, current_metrics: Dict[str, Any]) -> bool:
        """Write real-time metrics during test execution"""
        if not self.client and not self._connect_influxdb():
            logger.error("Cannot write real-time metrics: InfluxDB connection failed")
            return False
        
        try:
            # Add timestamp if not present
            if 'timestamp' not in current_metrics:
                current_metrics['timestamp'] = datetime.now(timezone.utc)
            
            point = self._create_influx_point(current_metrics)
            self.write_api.write(bucket=self.influxdb_bucket, record=point)
            
            logger.debug(f"Wrote real-time metrics: {current_metrics}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write real-time metrics: {e}")
            return False
    
    def write_test_status(self, phase: str, message: str, additional_data: Optional[Dict] = None) -> bool:
        """Write test status information to InfluxDB"""
        if not self.client and not self._connect_influxdb():
            return False
        
        try:
            point = Point("traffic_test_status") \
                .tag("test_name", self.test_name) \
                .tag("source_cpe", self.source_cpe) \
                .tag("destination_cpe", self.destination_cpe) \
                .tag("protocol", self.protocol) \
                .tag("pattern_type", self.pattern_type) \
                .field("phase", phase) \
                .field("message", message) \
                .time(datetime.now(timezone.utc), WritePrecision.NS)
            
            if additional_data:
                for key, value in additional_data.items():
                    if isinstance(value, (int, float)):
                        point = point.field(key, value)
                    else:
                        point = point.field(key, str(value))
            
            self.write_api.write(bucket=self.influxdb_bucket, record=point)
            logger.debug(f"Wrote test status: {phase} - {message}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write test status: {e}")
            return False
    
    def close(self):
        """Close InfluxDB connection"""
        if self.client:
            try:
                self.client.close()
                logger.info("Closed InfluxDB connection")
            except Exception as e:
                logger.error(f"Error closing InfluxDB connection: {e}")


class RealtimeMetricsCollector:
    """Collects metrics in real-time during traffic generation"""
    
    def __init__(self, metrics_collector: MetricsCollector, interval: int = 5):
        self.metrics_collector = metrics_collector
        self.interval = interval
        self.running = False
        self.current_metrics = {}
        
    async def start_collection(self, traffic_generator):
        """Start real-time metrics collection"""
        self.running = True
        logger.info(f"Starting real-time metrics collection (interval: {self.interval}s)")
        
        while self.running and traffic_generator.running:
            try:
                # Collect current metrics from traffic generator
                current_time = datetime.now(timezone.utc)
                
                # Basic metrics that are always available
                metrics = {
                    'timestamp': current_time,
                    'active_connections': len(traffic_generator.processes),
                    'test_duration': (current_time - traffic_generator.start_time).total_seconds() if traffic_generator.start_time else 0
                }
                
                # Try to get more detailed metrics from running processes
                # This is a simplified version - in practice, you might parse
                # intermediate iperf3 output or use other monitoring tools
                
                self.current_metrics = metrics
                
                # Write to InfluxDB
                self.metrics_collector.write_realtime_metrics(metrics)
                
                await asyncio.sleep(self.interval)
                
            except Exception as e:
                logger.error(f"Error in real-time metrics collection: {e}")
                await asyncio.sleep(self.interval)
    
    def stop_collection(self):
        """Stop real-time metrics collection"""
        self.running = False
        logger.info("Stopped real-time metrics collection")
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current metrics for status updates"""
        return self.current_metrics.copy()


async def main():
    """Main entry point for standalone testing"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Metrics Collector')
    parser.add_argument('--config', required=True, help='JSON configuration file')
    parser.add_argument('--results', required=True, help='iperf3 results JSON file')
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
        
        with open(args.results, 'r') as f:
            results = json.load(f)
        
        collector = MetricsCollector(config)
        
        # Write test status
        collector.write_test_status("Running", "Processing iperf3 results")
        
        # Write metrics
        success = collector.write_metrics(results.get('results', []))
        
        if success:
            collector.write_test_status("Completed", "Metrics successfully written to InfluxDB")
            print("Metrics successfully written to InfluxDB")
        else:
            collector.write_test_status("Failed", "Failed to write metrics to InfluxDB")
            print("Failed to write metrics to InfluxDB")
            sys.exit(1)
        
        collector.close()
        
    except Exception as e:
        logger.error(f"Metrics collection failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
