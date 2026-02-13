
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
        credentials, _ = google.auth.load_credentials_from_file(os.getenv("GOOGLE_APPLICATION_CREDENTIALS","/agent/networkagent.json"), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.client = spanner.Client(credentials=credentials)
        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)
        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        
    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps ending at now(), spaced by interval_minutes."""
        end_time = datetime.datetime.utcnow()
        timestamps = []
        for i in range(self.num_snapshots):
            delta = datetime.timedelta(minutes=self.interval_minutes * (self.num_snapshots - 1 - i))
            timestamps.append(end_time - delta)
        return timestamps

    def fetch_snapshot(self, timestamp: datetime.datetime) -> Dict:
        """
        Fetches the network state at `timestamp` and returns a JSON-compatible dict 
        matching the format expected by GraphBuilder.
        """
        snapshot_data = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}
        
        # Active filter for SCD Type 2
        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        with self.database.snapshot() as sn:
            # 1. Fetch Routers with ROLE
            # Map role to Node Type: PE Router, P Router, CE Router
            query_routers = f"""
                SELECT id, name, config, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            results = sn.execute_sql(query_routers, params=params, param_types=param_types)
            for row in results:
                role = row[3] if row[3] else "Unknown"
                # Map role to specific allowed types, default to "P Router" or "PE Router" if unknown?
                # User specified 3 types: PE, P, CE.
                # We assume 'role' column contains strings like "PE", "P", "CE".
                # If not, we might need normalization. Let's assume strict mapping for now or fallback.
                node_type = "P Router" # Fallback
                if role and "PE" in role.upper(): node_type = "PE Router"
                elif role and "CE" in role.upper(): node_type = "CE Router"
                elif role and "P" in role.upper(): node_type = "P Router"
                
                # Encode state/status?
                state_val = 1.0 if row[4] and row[4].lower() == "active" else 0.0
                
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": node_type,
                    "hostname": row[1],
                    "config": row[2] if row[2] else "",
                    "state": state_val
                })
                
            # 2. Fetch Interfaces
            query_interfaces = f"""
                SELECT id, router_id, name, speed, status
                FROM PhysicalInterface WHERE {valid_filter}
            """
            results = sn.execute_sql(query_interfaces, params=params, param_types=param_types)
            for row in results:
                state_val = 1.0 if row[4] and row[4].lower() == "up" else 0.0
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
            
            # 3. Router -> Interface Edges (Owns)
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    snapshot_data["edges"].append({
                        "source": node["device_id"],
                        "target": node["id"],
                        "relation": "Owns"
                    })

            # 4. Interface <-> Interface Edges (Connected)
            # Find interfaces sharing a link
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM Interface_Link il1
                JOIN Interface_Link il2 ON il1.link_id = il2.link_id
                WHERE il1.interface_id < il2.interface_id
                AND il1.valid_start_ts <= @ts AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                AND il2.valid_start_ts <= @ts AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            results = sn.execute_sql(query_links, params=params, param_types=param_types)
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

            # 5. Connect Metrics
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            params_metrics = {'t_start': t_start, 't_end': timestamp}
            param_types_metrics = {'t_start': spanner.param_types.TIMESTAMP, 't_end': spanner.param_types.TIMESTAMP}
            
            query_metrics = """
                SELECT interface_id, metrics
                FROM NetworkMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                ORDER BY timestamp DESC
            """
            
            results = sn.execute_sql(query_metrics, params=params_metrics, param_types=param_types_metrics)
            metrics_map = {} 
            
            for row in results:
                if row[0] not in metrics_map:
                    metrics_map[row[0]] = row[1]
            
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    if node["id"] in metrics_map:
                        m = metrics_map[node["id"]]
                        # metrics JSON assumed to have 'errors', 'rx_bps', 'tx_bps' etc.
                        # Adapting keys as needed
                        node["errors"] = float(m.get("errors", 0.0))
                        node["rx"] = float(m.get("rx_bps", 0.0)) # Assuming bps or similar
                        node["tx"] = float(m.get("tx_bps", 0.0))

        return snapshot_data
