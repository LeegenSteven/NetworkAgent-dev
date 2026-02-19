
import datetime
import logging
import google.auth
from google.cloud import spanner
from typing import List, Dict, Optional
import os

logger = logging.getLogger(__name__)

class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""
    
    def __init__(self, instance_id: str, database_id: str, num_snapshots: int = 50, interval_minutes: int = 5):
        logger.info(f"Initializing SpannerDataset: instance_id={instance_id}, database_id={database_id}, "
                   f"num_snapshots={num_snapshots}, interval_minutes={interval_minutes}")
        
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
        logger.debug(f"Loading credentials from: {creds_path}")
        credentials, _ = google.auth.load_credentials_from_file(creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        
        self.client = spanner.Client(credentials=credentials)
        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)
        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        
        logger.info("SpannerDataset initialized successfully")
        
    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps ending at now(), spaced by interval_minutes."""
        end_time = datetime.datetime.utcnow()
        logger.debug(f"Generating {self.num_snapshots} timestamps ending at {end_time.isoformat()}")
        
        timestamps = []
        for i in range(self.num_snapshots):
            delta = datetime.timedelta(minutes=self.interval_minutes * (self.num_snapshots - 1 - i))
            timestamps.append(end_time - delta)
        
        logger.info(f"Generated {len(timestamps)} timestamps from {timestamps[0].isoformat()} to {timestamps[-1].isoformat()}")
        return timestamps

    def fetch_snapshot(self, timestamp: datetime.datetime) -> Dict:
        """
        Fetches the network state at `timestamp` and returns a JSON-compatible dict 
        matching the format expected by GraphBuilder.
        """
        logger.info(f"Fetching snapshot for timestamp: {timestamp.isoformat()}")
        snapshot_data = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}
        
        # Active filter for SCD Type 2
        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        with self.database.snapshot(multi_use=True) as sn:
            # 1. Fetch Routers with ROLE
            # Map role to Node Type: PE Router, P Router, CE Router
            logger.debug("Querying PhysicalRouter table")
            query_routers = f"""
                SELECT id, name, config, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            results = sn.execute_sql(query_routers, params=params, param_types=param_types)
            router_count = 0
            router_types = {"PE Router": 0, "P Router": 0, "CE Router": 0}
            
            for row in results:
                role = row[3]
                
                # Default to P Router
                node_type = "P Router"
                
                # Normalize and map
                if role:
                    role_upper = role.upper()
                    if role_upper == "PE":
                         node_type = "PE Router"
                    elif role_upper == "CE":
                         node_type = "CE Router"
                    elif role_upper == "P":
                         node_type = "P Router"
                    # Legacy fallback
                    elif "PE" in role_upper:
                         node_type = "PE Router"
                    elif "CE" in role_upper:
                         node_type = "CE Router"
                
                router_types[node_type] += 1
                
                # Encode state/status?
                state_val = 1.0 if row[4] and row[4].lower() == "active" else 0.0
                
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": node_type,
                    "hostname": row[1],
                    "config": row[2] if row[2] else "",
                    "state": state_val
                })
                router_count += 1
            
            logger.info(f"Fetched {router_count} routers - PE: {router_types['PE Router']}, "
                       f"P: {router_types['P Router']}, CE: {router_types['CE Router']}")
                
            # 2. Fetch Interfaces
            logger.debug("Querying PhysicalInterface table")
            query_interfaces = f"""
                SELECT id, router_id, name, speed, status
                FROM PhysicalInterface WHERE {valid_filter}
            """
            results = sn.execute_sql(query_interfaces, params=params, param_types=param_types)
            interface_count = 0
            interface_up_count = 0
            
            for row in results:
                state_val = 1.0 if row[4] and row[4].lower() == "up" else 0.0
                if state_val == 1.0:
                    interface_up_count += 1
                    
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": "Interface",
                    "name": row[2],
                    "device_id": row[1],
                    "state": state_val,
                    "errors": 0.0,      # Placeholder
                    "rx": 0.0,          # Placeholder
                    "tx": 0.0           # Placeholder
                })
                interface_count += 1
            
            logger.info(f"Fetched {interface_count} interfaces ({interface_up_count} up, {interface_count - interface_up_count} down)")
            
            # 3. Router -> Interface Edges (Owns)
            logger.debug("Creating Router -> Interface 'Owns' edges")
            owns_edge_count = 0
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    snapshot_data["edges"].append({
                        "source": node["device_id"],
                        "target": node["id"],
                        "relation": "Owns"
                    })
                    owns_edge_count += 1
            
            logger.debug(f"Created {owns_edge_count} 'Owns' edges")

            # 4. Interface <-> Interface Edges (Connected)
            # Find interfaces sharing a link
            logger.debug("Querying Interface_Link table for 'Connected' edges")
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM Interface_Link il1
                JOIN Interface_Link il2 ON il1.link_id = il2.link_id
                WHERE il1.interface_id < il2.interface_id
                AND il1.valid_start_ts <= @ts AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                AND il2.valid_start_ts <= @ts AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            results = sn.execute_sql(query_links, params=params, param_types=param_types)
            connected_edge_count = 0
            
            for row in results:
                # Add bidirectional 'Connected' edge
                snapshot_data["edges"].append({
                    "source": row[0],
                    "target": row[1],
                    "relation": "Connected"
                })
                snapshot_data["edges"].append({
                    "source": row[1],
                    "target": row[0],
                    "relation": "Connected"
                })
                connected_edge_count += 2
            
            logger.debug(f"Created {connected_edge_count} 'Connected' edges")

            # 5. Connect Metrics
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            logger.debug(f"Querying NetworkMetrics table for time range: {t_start.isoformat()} to {timestamp.isoformat()}")
            
            # Gather physical router names and map interface properties
            physical_router_names = []
            router_id_to_hostname = {}
            for node in snapshot_data["nodes"]:
                if node["type"] in ["PE Router", "P Router", "CE Router"]:
                    if node.get("hostname"):
                        physical_router_names.append(node["hostname"])
                        router_id_to_hostname[node["id"]] = node["hostname"]
            
            interface_nodes = [n for n in snapshot_data["nodes"] if n["type"] == "Interface"]
            
            if physical_router_names:
                params_metrics = {
                    "t_start": t_start, 
                    "t_end": timestamp,
                    "node_names": physical_router_names
                }
                param_types_metrics = {
                    "t_start": spanner.param_types.TIMESTAMP, 
                    "t_end": spanner.param_types.TIMESTAMP,
                    "node_names": spanner.param_types.Array(spanner.param_types.STRING)
                }
                
                query_metrics = """
                    SELECT node_name, interface, metric_name, AVG(value) as agg_value
                    FROM NetworkMetrics
                    WHERE timestamp > @t_start AND timestamp <= @t_end
                    AND node_name IN UNNEST(@node_names)
                    AND interface IS NOT NULL
                    GROUP BY node_name, interface, metric_name
                """
                
                results = sn.execute_sql(query_metrics, params=params_metrics, param_types=param_types_metrics)
                
                # Dictionary mapping (node_name, interface_name) -> {metric_name: agg_value}
                metrics_map = {} 
                
                for row in results:
                    node_name = row[0]
                    interface_name = row[1]
                    metric_name = row[2]
                    value = row[3]
                    
                    key = (node_name, interface_name)
                    if key not in metrics_map:
                        metrics_map[key] = {}
                    
                    metrics_map[key][metric_name] = value
                
                logger.debug(f"Fetched metrics for {len(metrics_map)} interfaces")
                
                metrics_applied = 0
                for node in interface_nodes:
                    hostname = router_id_to_hostname.get(node.get("device_id"))
                    if not hostname:
                        continue
                    
                    key = (hostname, node.get("name"))
                    if key in metrics_map:
                        m = metrics_map[key]
                        node["errors"] = float(m.get("node_network_receive_errs_total", 0.0)) + float(m.get("node_network_transmit_errs_total", 0.0))
                        node["rx"] = float(m.get("node_network_receive_bytes_total", 0.0))
                        node["tx"] = float(m.get("node_network_transmit_bytes_total", 0.0))
                        metrics_applied += 1
                
                logger.info(f"Applied metrics to {metrics_applied}/{interface_count} interfaces")

        total_edges = len(snapshot_data["edges"])
        total_nodes = len(snapshot_data["nodes"])
        logger.info(f"Snapshot complete: {total_nodes} nodes, {total_edges} edges at {timestamp.isoformat()}")
        
        return snapshot_data
