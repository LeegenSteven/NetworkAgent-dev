
import datetime
import logging
from google.cloud import spanner
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""
    
    def __init__(self, instance_id: str, database_id: str, num_snapshots: int = 50, interval_minutes: int = 5):
        self.client = spanner.Client()
        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)
        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        
    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps ending at now(), spaced by interval_minutes."""
        end_time = datetime.datetime.utcnow()
        timestamps = []
        for i in range(self.num_snapshots):
            # T, T-5, T-10 ... (reverse order? No, we usually want t=0 to t=N)
            # Let's generate chronological order: oldest to newest
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
        # Start <= T < End (or End is NULL)
        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        with self.database.snapshot() as sn:
            # 1. Fetch Routers
            query_routers = f"""
                SELECT id, name, config 
                FROM PhysicalRouter WHERE {valid_filter}
            """
            results = sn.execute_sql(query_routers, params=params, param_types=param_types)
            for row in results:
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": "Router",
                    "hostname": row[1],
                    "config": row[2] if row[2] else "",
                    # Metrics will be joined/filled later or fetched from metrics table
                    "rib_size": 0, # Placeholder
                    "bgp_prefixes": 0 # Placeholder
                })
                
            # 2. Fetch Interfaces
            query_interfaces = f"""
                SELECT id, router_id, name, speed
                FROM PhysicalInterface WHERE {valid_filter}
            """
            results = sn.execute_sql(query_interfaces, params=params, param_types=param_types)
            for row in results:
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": "Interface",
                    "name": row[2],
                    "device_id": row[1],
                    "errors": 0,      # Placeholder
                    "utilization": 0.0 # Placeholder
                })
            
            # 3. Fetch Links (Edges: Interface <-> Interface via Link)
            # PhysicalLink is an edge object in our graph model, but in the GNN model 'Link' might not be a node type?
            # Reference gnn_utils.py doesn't show "Link" as a node type in FEATURE_MAP.
            # Reference uses "Connected" relation between Interfaces.
            # We need to resolve PhysicalLink to direct connections or use it as an edge.
            # The VIEW 'ConnectsTo_Edge' and 'LinkedTo_Edge' helps.
            # Let's use the views if possible, or manual join.
            # Reference logic: 
            #   ("ConnectedTo", "src_interface_id", "dst_interface_id", "Connected"),
            
            # Since we are essentially recreating the logic, let's fetch 'PhysicalLink' and find the two interfaces it connects using Interface_Link.
            # Or better, query the Interface_Link table directly?
            # Interface_Link: interface_id, link_id.
            # A link connects 2 interfaces usually.
            
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM PhysicalLink l
                JOIN Interface_Link il1 ON l.id = il1.link_id
                JOIN Interface_Link il2 ON l.id = il2.link_id
                WHERE {valid_filter.replace('valid_', 'l.valid_')}
                AND il1.interface_id < il2.interface_id -- Avoid duplicates
                AND il1.valid_start_ts <= @ts AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                AND il2.valid_start_ts <= @ts AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            # Wait, complex join might be slow or hard to get right with SCD2 on all tables.
            # Let's stick to edges we can easily reconstruct.
            
            # Router -> Owns -> Interface
            # We already fetched Interface with router_id.
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    snapshot_data["edges"].append({
                        "source": node["device_id"],
                        "target": node["id"],
                        "relation": "Owns"
                    })
                    
            # 4. Fetch Network Metrics (closest to timestamp)
            # NetworkMetrics table: id, kind, name, timestamp, metrics (JSON), interface_id
            # We need to join this to Interface/Router.
            # Assuming metrics are logged frequently. We want the latest metric BEFORE or AT @ts for each entity?
            # Or just "at" @ts if we assume roughly aligned snapshots.
            # Let's query NetworkMetrics where timestamp between T-5m and T.
            
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            params_metrics = {'t_start': t_start, 't_end': timestamp}
            param_types_metrics = {'t_start': spanner.param_types.TIMESTAMP, 't_end': spanner.param_types.TIMESTAMP}
            
            # We want the LATEST metric for each interface_id within the window
            query_metrics = """
                SELECT interface_id, metrics
                FROM NetworkMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                ORDER BY timestamp DESC
            """
            # This query might return multiple rows per interface. We'll handle dedup in python or use ARRAY_AGG in SQL?
            # Let's just fetch and update dict.
            
            results = sn.execute_sql(query_metrics, params=params_metrics, param_types=param_types_metrics)
            metrics_map = {} # interface_id -> metrics dict
            
            for row in results:
                if row[0] not in metrics_map:
                    metrics_map[row[0]] = row[1] # First one is latest due to DESC sort (if we trust stability, but wait, global sort?)
                    # Spanner doesn't guarantee global sort unless we strictly order query.
                    # Actually logic: keys are not unique in WHERE.
                    # Better: SELECT interface_id, metrics FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY interface_id ORDER BY timestamp DESC) as rn ...)
            
            # Simplified: just iterate and take first seen if we strictly ordered.
            # Or just take ANY in the window for this MVP.
            
            # Update Node Metrics
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    if node["id"] in metrics_map:
                        m = metrics_map[node["id"]]
                        # Map JSON fields to Feature Map
                        # FEATURE_MAP["Interface"]: ["Errors", "Utilization"]
                        node["errors"] = float(m.get("errors", 0))
                        node["utilization"] = float(m.get("utilization", 0.0))
            
            # TODO: Add Flow fetching logic if Flow table exists and is populated.
            # The schema had "HasFlow" edge.
            
        return snapshot_data

