"""Real loopback BubbleRAN replay into the local governance service."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Iterator

import duckdb
import httpx
import uvicorn

from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance
from telco_domain import Incident, IncidentStatus
from telco_lab import (
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
    BUBBLERAN_SOURCE_LICENSE,
    DownloadReceipt,
    FixtureCatalogProvider,
    LoopbackHttpReplaySink,
    ReplayPolicy,
    TelcoLab,
    adapt_bubbleran_persistent_interference_csv,
    build_replay_plan,
    run_paced_replay,
)


ROOT = Path(__file__).resolve().parents[3]
SOURCE_START = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
REPLAY_START = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
RESOURCE_ID = "bubbleran.persistent-interference.anomalous.v1"
SCENARIO = "bubbleran-persistent-interference"
LOCAL_ENVIRONMENT = {"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"}


@dataclass
class _MutableClock:
    instant: datetime

    def __call__(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


class _MemoryDownloader:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.calls = 0

    def download(self, resource, target: Path) -> DownloadReceipt:  # noqa: ANN001
        self.calls += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._body)
        return DownloadReceipt(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=False,
        )


def _bubbleran_csv() -> bytes:
    headers = [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        "timestamp_iso",
        "persistent_anomaly",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row_index in range(4):
        instant = SOURCE_START + timedelta(seconds=row_index)
        row: dict[str, object] = {
            "": row_index,
            "timestamp": int(instant.timestamp()),
            "ran_ue_id": "answer-key-only-source",
            "e2node_nb_id": "50",
            "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
            "persistent_anomaly": "True",
        }
        row.update(
            {
                name: f"{(metric_index + row_index) / 100:.2f}"
                for metric_index, name in enumerate(
                    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                    start=1,
                )
            }
        )
        row["mac_ul_bler"] = "0.20"
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _catalog(body: bytes) -> FixtureCatalogProvider:
    return FixtureCatalogProvider(
        {
            "schema_version": "1.0",
            "catalog_id": "bubbleran-governance-e2e",
            "catalog_version": "1.0.0",
            "resources": [
                {
                    "resource_id": RESOURCE_ID,
                    "dataset_id": BUBBLERAN_DATASET_ID,
                    "dataset_version": BUBBLERAN_DATASET_VERSION,
                    "filename": "anomalous.csv",
                    "source_url": "https://fixtures.example.test/anomalous.csv",
                    "allowed_hosts": ["fixtures.example.test"],
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "media_type": "text/csv",
                    "adapter": BUBBLERAN_CSV_ADAPTER_ID,
                    "license": {
                        "id": BUBBLERAN_SOURCE_LICENSE,
                        "name": "Creative Commons Attribution-ShareAlike 4.0",
                        "url": "https://creativecommons.org/licenses/by-sa/4.0/",
                        "evidence_url": "https://fixtures.example.test/LICENSE",
                        "evidence_sha256": "a" * 64,
                        "attribution": "BubbleRAN dataset authors",
                        "reviewed_at": "2026-08-30",
                        "acceptance_required": True,
                    },
                }
            ],
        }
    )


@contextmanager
def _serve(app, *, port: int) -> Iterator[int]:  # noqa: ANN001
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="critical",
            lifespan="off",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise AssertionError("loopback server did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def _database_counts(database_path: Path) -> tuple[int, int, int, int]:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "canonical_incidents",
                "canonical_incident_audit",
                "canonical_incident_source_events",
                "canonical_incident_idempotency",
            )
        )
    finally:
        connection.close()


async def _prepare(client: httpx.AsyncClient, incident_id: str, suffix: str) -> dict:
    response = await client.post(
        f"/local/v1/incidents/{incident_id}/prepare",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"prepare-bubbleran-{suffix}",
            "actor": "local-governance",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["incident"]["status"] == "AWAITING_APPROVAL"
    assert data["rca"]["conclusion"] == "CONCLUSIVE"
    assert data["action"]["action_type"] == "LOCAL_SIMULATION"
    assert data["approval"]["status"] == "PENDING"
    return data


async def _decide(
    client: httpx.AsyncClient,
    incident_id: str,
    suffix: str,
    prepared: dict,
    *,
    approve: bool,
) -> dict:
    response = await client.post(
        f"/local/v1/incidents/{incident_id}/decide",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"decide-bubbleran-{suffix}",
            "actor": "local-e2e-operator",
            "reason": "Review the exact side-effect-free local simulation",
            "approve": approve,
            "expected_action_hash": prepared["action"]["action_hash"],
            "expected_revision": prepared["incident"]["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _execute(
    client: httpx.AsyncClient,
    incident_id: str,
    suffix: str,
    *,
    verification_passed: bool,
) -> dict:
    response = await client.post(
        f"/local/v1/incidents/{incident_id}/execute",
        headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
        json={
            "idempotency_key": f"execute-bubbleran-{suffix}",
            "actor": "local-e2e-operator",
            "verification_passed": verification_passed,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_bubbleran_real_tcp_replay_closes_all_local_governance_branches(
    tmp_path: Path,
) -> None:
    body = _bubbleran_csv()
    provider = _catalog(body)
    downloader = _MemoryDownloader(body)
    lab = TelcoLab(provider, tmp_path / "lab", downloader=downloader)  # type: ignore[arg-type]
    artifact = lab.fetch(RESOURCE_ID, accepted_license=BUBBLERAN_SOURCE_LICENSE)
    bundle = adapt_bubbleran_persistent_interference_csv(artifact.local_path)
    assert downloader.calls == 1
    assert bundle.manifest.observation_count == 4

    clock = _MutableClock(REPLAY_START + timedelta(minutes=1))
    database_path = tmp_path / "local-governance.duckdb"

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    selected_port = int(listener.getsockname()[1])
    listener.close()
    config = AssuranceConfig(
        database_path=database_path,
        performance_csv_path=ROOT / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=ROOT / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=ROOT / "data/rca-rules/lte",
        documents_dir=ROOT / "data/docs/lte",
        public_url=f"http://127.0.0.1:{selected_port}/",
        actor="local-assurance-service",
        host="127.0.0.1",
        port=selected_port,
    )
    initialize_assurance(config, clock=clock)
    app = create_app(config, clock=clock)

    with _serve(app, port=selected_port) as port:
        endpoint = f"http://127.0.0.1:{port}/local/v1/faults/replay"
        policy = ReplayPolicy(
            endpoint=endpoint,
            action_mode="disabled",
            speed=100,
            max_events=4,
            max_rate_per_second=100,
            max_duration_seconds=30,
            max_payload_bytes=64 * 1024,
            max_total_payload_bytes=512 * 1024,
            max_resources=1,
            max_concurrency=1,
        )
        plan = build_replay_plan(
            lab,
            bundle,
            scenario=SCENARIO,
            replay_window_start=REPLAY_START,
            policy=policy,
            environ=LOCAL_ENVIRONMENT,
        )
        assert len(plan.events) == 4
        serialized_wire = json.dumps(
            [event.sink_payload() for event in plan.events],
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        for forbidden in (
            "persistent_anomaly",
            "ground_truth",
            "answer-key-only-source",
            "ran_ue_id",
            "e2node_nb_id",
        ):
            assert forbidden not in serialized_wire

        sink = LoopbackHttpReplaySink(
            policy,
            environ=LOCAL_ENVIRONMENT,
            timeout_seconds=5,
        )
        first_delivery = asyncio.run(run_paced_replay(plan, sink))
        assert first_delivery.plan_complete is True
        assert first_delivery.delivered_count == 4
        assert first_delivery.retry_count == 0
        assert first_delivery.error_code is None

        repository = app.state.assurance_components.profile.incident_repository

        async def govern() -> tuple[Incident, ...]:
            incidents = []
            for event in plan.events:
                incident = await repository.find_active(
                    source_event_id=event.source_event_id
                )
                assert incident is not None
                assert incident.technology.value == "5G_SA"
                assert len(incident.violated_kpis) == 1
                assert incident.violated_kpis[0].kpi_name == "ran.mac.ul_bler"
                incidents.append(incident)

            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                trust_env=False,
            ) as client:
                success = await _prepare(client, incidents[0].incident_id, "success")
                approved_success = await _decide(
                    client,
                    incidents[0].incident_id,
                    "success",
                    success,
                    approve=True,
                )
                assert approved_success["incident"]["status"] == "REMEDIATING"
                resolved = await _execute(
                    client,
                    incidents[0].incident_id,
                    "success",
                    verification_passed=True,
                )
                assert resolved["incident"]["status"] == "RESOLVED"
                assert resolved["verification"]["status"] == "PASSED"

                failure = await _prepare(
                    client, incidents[1].incident_id, "verification-failure"
                )
                await _decide(
                    client,
                    incidents[1].incident_id,
                    "verification-failure",
                    failure,
                    approve=True,
                )
                reopened = await _execute(
                    client,
                    incidents[1].incident_id,
                    "verification-failure",
                    verification_passed=False,
                )
                assert reopened["incident"]["status"] == "REOPENED"
                assert reopened["verification"]["status"] == "FAILED"

                rejection = await _prepare(
                    client, incidents[2].incident_id, "rejection"
                )
                rejected = await _decide(
                    client,
                    incidents[2].incident_id,
                    "rejection",
                    rejection,
                    approve=False,
                )
                assert rejected["incident"]["status"] == "REJECTED"
                assert rejected["action_runs"] == []
                assert rejected["verification"] is None

                expiry = await _prepare(client, incidents[3].incident_id, "expiry")
                await _decide(
                    client,
                    incidents[3].incident_id,
                    "expiry",
                    expiry,
                    approve=True,
                )
                clock.advance(timedelta(minutes=16))
                expired = await _execute(
                    client,
                    incidents[3].incident_id,
                    "expiry",
                    verification_passed=True,
                )
                assert expired["incident"]["status"] == "FAILED"
                assert expired["action_runs"] == []
                assert expired["verification"] is None

            persisted = []
            for incident in incidents:
                persisted.append(await repository.get(incident.incident_id))
            assert all(item is not None for item in persisted)
            return tuple(item for item in persisted if item is not None)

        governed = asyncio.run(govern())
        assert tuple(item.status for item in governed) == (
            IncidentStatus.RESOLVED,
            IncidentStatus.REOPENED,
            IncidentStatus.REJECTED,
            IncidentStatus.FAILED,
        )
        assert sum(len(item.action_runs) for item in governed) == 2
        assert sum(len(item.verification_runs) for item in governed) == 2
        assert all(
            run.metadata == {"mode": "simulation", "side_effects": False}
            for item in governed
            for run in item.action_runs
        )

        before_replay = _database_counts(database_path)
        second_delivery = asyncio.run(run_paced_replay(plan, sink))
        assert second_delivery.plan_complete is True
        assert second_delivery.delivered_count == 4
        assert second_delivery.retry_count == 0
        assert _database_counts(database_path) == before_replay

        async def persisted_after_replay() -> tuple[Incident, ...]:
            result = []
            for item in governed:
                current = await repository.get(item.incident_id)
                assert current is not None
                result.append(current)
            return tuple(result)

        assert asyncio.run(persisted_after_replay()) == governed
