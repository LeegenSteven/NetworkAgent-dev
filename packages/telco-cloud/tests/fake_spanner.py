from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from datetime import UTC, datetime


KEY_COLUMNS = {
    "CanonicalIncidentsV2": ("incident_id",),
    "CanonicalIncidentSourceEventsV2": ("incident_id", "source_event_id"),
    "CanonicalIncidentAuditV2": ("incident_id", "revision"),
    "CanonicalIncidentIdempotencyV2": (
        "operation",
        "requested_incident_id",
        "idempotency_key",
    ),
    "CanonicalIncidentActiveKeysV2": ("key_hash",),
    "CanonicalSourceEventInboxV2": ("source_event_id",),
    "CanonicalIncidentOutboxV2": ("event_id",),
    "RadioKpiObservationsV1": ("observation_id",),
    "SafeEvidenceReferencesV1": ("evidence_id",),
    "CanonicalResourceReferencesV1": ("resource_id",),
}


def _keys(keyset):
    return tuple(tuple(item) for item in getattr(keyset, "keys", ()))


class FakeReader:
    def __init__(self, tables):
        self.tables = tables

    def read(self, table, columns, keyset, **kwargs):
        del kwargs
        rows = self.tables[table]
        selected = []
        requested = _keys(keyset)
        if requested:
            for key in requested:
                row = rows.get(key)
                if row is not None:
                    selected.append(row)
        elif getattr(keyset, "all_", False):
            selected = list(rows.values())
        return [tuple(row.get(column) for column in columns) for row in selected]

    def execute_sql(self, sql, params=None, **kwargs):
        del kwargs
        params = params or {}
        if "telco-cloud:list-incidents" in sql:
            rows = list(self.tables["CanonicalIncidentsV2"].values())
            if "status" in params:
                rows = [row for row in rows if row["status"] == params["status"]]
            rows.sort(key=lambda row: row["incident_id"])
            offset = int(params.get("offset", 0))
            limit = int(params["limit"])
            columns = (
                "incident_id",
                "correlation_key",
                "schema_version",
                "technology",
                "status",
                "severity",
                "revision",
                "trace_id",
                "detected_at",
                "created_at",
                "updated_at",
                "payload",
            )
            return [
                tuple(row[column] for column in columns)
                for row in rows[offset : offset + limit]
            ]
        if "telco-cloud:history" in sql:
            rows = [
                row
                for row in self.tables["CanonicalIncidentAuditV2"].values()
                if row["incident_id"] == params["incident_id"]
            ]
            rows.sort(key=lambda row: (row["revision"], row["event_id"]))
            if "limit" in params:
                offset = int(params.get("offset", 0))
                rows = rows[offset : offset + int(params["limit"])]
            columns = (
                "incident_id",
                "revision",
                "event_id",
                "from_status",
                "to_status",
                "trace_id",
                "occurred_at",
                "payload",
            )
            return [tuple(row[column] for column in columns) for row in rows]
        if "telco-cloud:source-associations" in sql:
            rows = [
                row
                for row in self.tables["CanonicalIncidentSourceEventsV2"].values()
                if row["incident_id"] == params["incident_id"]
            ]
            rows.sort(key=lambda row: row["source_event_id"])
            offset = int(params.get("offset", 0))
            rows = rows[offset : offset + int(params["limit"])]
            columns = (
                "incident_id",
                "source_event_id",
                "registered_at",
                "actor",
                "reason",
                "idempotency_key",
                "trace_id",
                "payload",
            )
            return [tuple(row[column] for column in columns) for row in rows]
        if "telco-cloud:migration-active-keys-for-incident" in sql:
            rows = [
                row
                for row in self.tables["CanonicalIncidentActiveKeysV2"].values()
                if row["incident_id"] == params["incident_id"]
            ]
            rows.sort(key=lambda row: row["key_hash"])
            return [
                (row["key_hash"], row["key_kind"])
                for row in rows[: int(params["active_key_limit"])]
            ]
        if "telco-cloud:active-keys-for-incident" in sql:
            rows = [
                row
                for row in self.tables["CanonicalIncidentActiveKeysV2"].values()
                if row["incident_id"] == params["incident_id"]
            ]
            return [
                (row["key_hash"],)
                for row in rows[: int(params["active_key_limit"])]
            ]
        if "telco-cloud:source-events-for-incident" in sql:
            rows = [
                row
                for row in self.tables["CanonicalIncidentSourceEventsV2"].values()
                if row["incident_id"] == params["incident_id"]
            ]
            rows.sort(key=lambda row: row["source_event_id"])
            return [
                (row["source_event_id"],)
                for row in rows[: int(params["source_event_limit"])]
            ]
        if "telco-cloud:active-keys-by-hash" in sql:
            requested = frozenset(params["key_hashes"])
            rows = [
                row
                for row in self.tables["CanonicalIncidentActiveKeysV2"].values()
                if row["key_hash"] in requested
            ]
            rows.sort(key=lambda row: row["key_hash"])
            return [
                (row["key_hash"], row["key_kind"], row["incident_id"])
                for row in rows
            ]
        if "telco-cloud:source-event-owners" in sql:
            requested = frozenset(params["source_event_ids"])
            rows = [
                row
                for row in self.tables[
                    "CanonicalIncidentSourceEventsV2"
                ].values()
                if row["source_event_id"] in requested
            ]
            rows.sort(key=lambda row: (row["source_event_id"], row["incident_id"]))
            return [
                (row["source_event_id"], row["incident_id"])
                for row in rows
            ]
        if "telco-cloud:source-event-owner" in sql:
            rows = [
                row
                for row in self.tables[
                    "CanonicalIncidentSourceEventsV2"
                ].values()
                if row["source_event_id"] == params["source_event_id"]
            ]
            rows.sort(key=lambda row: row["incident_id"])
            return [(row["incident_id"],) for row in rows[:2]]
        if "telco-cloud:claim-outbox" in sql:
            now = params["trusted_now"]
            rows = [
                row
                for row in self.tables["CanonicalIncidentOutboxV2"].values()
                if (
                    row["status"] == "PENDING"
                    and row["available_at"] <= now
                )
                or (
                    row["status"] == "LEASED"
                    and row.get("lease_expires_at") is not None
                    and row["lease_expires_at"] <= now
                )
            ]
            rows.sort(key=lambda row: (row["available_at"], row["event_id"]))
            columns = (
                "event_id",
                "incident_id",
                "source_event_id",
                "event_type",
                "payload",
                "status",
                "attempts",
                "available_at",
                "created_at",
                "published_at",
                "lease_owner",
                "lease_expires_at",
                "last_error_code",
            )
            return [
                tuple(row.get(column) for column in columns)
                for row in rows[: int(params["limit"])]
            ]
        if "telco-cloud:query-kpis" in sql:
            rows = list(self.tables["RadioKpiObservationsV1"].values())
            rows = [
                row
                for row in rows
                if row["technology"] == params["technology"]
                and row["kpi_name"] in params["kpi_names"]
                and params["window_start"] <= row["observed_at"] <= params["window_end"]
            ]
            if params.get("resource_ids"):
                rows = [
                    row
                    for row in rows
                    if row["primary_resource_id"] in params["resource_ids"]
                ]
            rows.sort(key=lambda row: (row["observed_at"], row["observation_id"]))
            return [
                (
                    row["observation_id"],
                    row["kpi_name"],
                    row["technology"],
                    row["primary_resource_id"],
                    row["observed_at"],
                    row["payload"],
                )
                for row in rows[: int(params["limit"])]
            ]
        if "telco-cloud:collect-evidence" in sql:
            rows = [
                row
                for row in self.tables["SafeEvidenceReferencesV1"].values()
                if row["incident_id"] == params["incident_id"]
                and params["window_start"] <= row["collected_at"] <= params["window_end"]
            ]
            rows.sort(key=lambda row: (row["collected_at"], row["evidence_id"]))
            return [
                (
                    row["incident_id"],
                    row["evidence_id"],
                    row["evidence_type"],
                    row["collected_at"],
                    row["payload"],
                )
                for row in rows[: int(params["limit"])]
            ]
        if "telco-cloud:resolve-resources" in sql:
            rows = [
                row
                for row in self.tables["CanonicalResourceReferencesV1"].values()
                if row["resource_id"] in params["resource_ids"]
            ]
            if "technology" in params:
                rows = [
                    row for row in rows if row["technology"] == params["technology"]
                ]
            rows.sort(key=lambda row: row["resource_id"])
            return [
                (
                    row["resource_id"],
                    row["technology"],
                    row["resource_type"],
                    row["payload"],
                )
                for row in rows[: int(params["limit"])]
            ]
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeTransaction(FakeReader):
    def __init__(self, tables, *, fail_table=None):
        super().__init__(tables)
        self.fail_table = fail_table

    def insert(self, table, columns, values):
        if table == self.fail_table:
            raise RuntimeError(f"injected failure for {table}")
        key_columns = KEY_COLUMNS[table]
        for values_row in values:
            row = dict(zip(columns, values_row, strict=True))
            key = tuple(row[name] for name in key_columns)
            if key in self.tables[table]:
                raise RuntimeError(f"duplicate key for {table}: {key}")
            if table == "CanonicalIncidentSourceEventsV2" and any(
                existing["source_event_id"] == row["source_event_id"]
                for existing in self.tables[table].values()
            ):
                raise RuntimeError(
                    "duplicate source_event_id for "
                    "CanonicalIncidentSourceEventsV2"
                )
            self.tables[table][key] = row

    def update(self, table, columns, values):
        key_columns = KEY_COLUMNS[table]
        for values_row in values:
            changes = dict(zip(columns, values_row, strict=True))
            key = tuple(changes[name] for name in key_columns)
            if key not in self.tables[table]:
                raise RuntimeError(f"missing key for {table}: {key}")
            self.tables[table][key].update(changes)

    def delete(self, table, keyset):
        for key in _keys(keyset):
            self.tables[table].pop(key, None)


class FakeDatabase:
    def __init__(self):
        self.tables = {name: {} for name in KEY_COLUMNS}
        self.fail_table = None
        self._lock = threading.RLock()

    def run_in_transaction(self, callback):
        with self._lock:
            working = copy.deepcopy(self.tables)
            transaction = FakeTransaction(working, fail_table=self.fail_table)
            result = callback(transaction)
            self.tables = working
            return result

    @contextmanager
    def snapshot(self, **kwargs):
        del kwargs
        with self._lock:
            yield FakeReader(copy.deepcopy(self.tables))

    def seed(self, table: str, row: dict[str, object]) -> None:
        key = tuple(row[name] for name in KEY_COLUMNS[table])
        self.tables[table][key] = copy.deepcopy(row)

    def count(self, table: str) -> int:
        return len(self.tables[table])


class RetryingFakeDatabase(FakeDatabase):
    """Run the callback twice from the same pre-transaction state.

    The first attempt models a Spanner ABORTED transaction whose mutations are
    discarded. Only the second attempt is committed.
    """

    def __init__(self):
        super().__init__()
        self.attempt_results = ()

    def run_in_transaction(self, callback):
        with self._lock:
            initial = copy.deepcopy(self.tables)
            results = []
            committed = None
            for _ in range(2):
                working = copy.deepcopy(initial)
                transaction = FakeTransaction(working, fail_table=self.fail_table)
                results.append(callback(transaction))
                committed = working
            assert committed is not None
            self.tables = committed
            self.attempt_results = tuple(results)
            return results[-1]


NOW = datetime(2035, 1, 1, 12, tzinfo=UTC)
