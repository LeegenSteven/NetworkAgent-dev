from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect

from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance
from telco_assurance_agent.business_boundary import (
    LocalBusinessOperationBoundary,
    LocalHttpRequestAdmission,
)
from telco_assurance_agent.fault_receiver import (
    LocalReplayFaultReceiver,
    fault_receiver_routes,
)
from telco_domain import IncidentStatus
from telco_lab import (
    ReplayEvent,
    ReplayWirePayload,
    canonical_json_bytes,
    validate_replay_wire_payload,
)
from telco_local import (
    BUBBLERAN_REPLAY_DETECTOR_ALGORITHM,
    BUBBLERAN_REPLAY_RULE_ID,
    RcaRule,
    rule_content_sha256,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 30, 8, 5, tzinfo=UTC)
REPLAY_OBSERVED_AT = datetime(2026, 8, 30, 8, 1, 23, tzinfo=UTC)
EVENT_IDENTITIES = {
    "3": (
        "23e9b94bceee09bf2fbc552fd151d92803d60aef535329d444e70e502b12650f",
        "labevent-156348cb06ecf498e4a66c0e67105610c9c281d8ea207cbe329b6f42aba31aaf",
        "labidempotency-2396126763d3e8314295e4a2e9fb2deca010d7dafc80c3783a193e4b3c495153",
        REPLAY_OBSERVED_AT,
        "ran.mac.dl_bler",
        0.125,
        "bubbleran-persistent-interference",
        "3",
    ),
    "4": (
        "8a92aa1c0f3b275ea9649d03694967a9e61d9418adfffc57fd48a219ab81eb49",
        "labevent-7500fe05060a1f9a60f9d39b6dcf5ffe625a981707d9a2fc8a5fe3baede2a537",
        "labidempotency-9eca1a7d1805a872e7339d46aeb30982ced14f935c0fbf7e56518cdb561c4b4c",
        datetime(2026, 8, 30, 8, 1, 24, tzinfo=UTC),
        "ran.mac.dl_bler",
        0.875,
        "bubbleran-persistent-interference",
        "4",
    ),
    "5": (
        "362aa13a92c6f4cc3ec14c66a6510b9f540499fe7ac3a41b9a548de9c036b498",
        "labevent-e4b2d70c0f9d9ef4084e78116c571284b537112c6d1afb4b0654e2e27b40ceb7",
        "labidempotency-3d647645444e33dd6c78835cc0fdd6be8459ab66f9d4f0d51779eab6d6b340e3",
        datetime(2026, 8, 30, 8, 1, 25, tzinfo=UTC),
        "ran.mac.ul_bler",
        0.2,
        "bubbleran-persistent-interference",
        "5",
    ),
    "6": (
        "4ce75d96df83c65913b3b0eabd28ebf263f63a54e8e6aca3d3427dadfc2ef3fd",
        "labevent-0ba03e4fc5e6988e02cf99da7bcafd658fb4441af7e738ceab057f79030df90e",
        "labidempotency-f97a297abcf1e919e1e19dd23997f602a80592cdbf5f3e18a2c579d89102861c",
        datetime(2026, 8, 30, 8, 1, 26, tzinfo=UTC),
        "ran.mac.ul_bler",
        0.15,
        "bubbleran-persistent-interference",
        "6",
    ),
    "wrong-scenario": (
        "5c8506198d5a0150d2ce350057a08460e2f378b01352e43e7e08cdaf505b59f4",
        "labevent-a58515a48c2c5eeffe4b1af4292e2a263dc55f7d237314d090fb0fa8e8a17360",
        "labidempotency-4a7178976faf895f5a7f0d58492e12b0347a90a0c14973dc8fbdaef42c785472",
        REPLAY_OBSERVED_AT,
        "ran.mac.dl_bler",
        0.125,
        "assurance-demo",
        "3",
    ),
}


def _config(tmp_path: Path) -> AssuranceConfig:
    return AssuranceConfig(
        database_path=tmp_path / "local.duckdb",
        performance_csv_path=REPOSITORY_ROOT / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=REPOSITORY_ROOT
        / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=REPOSITORY_ROOT / "data/rca-rules/lte",
        public_url="http://127.0.0.1:8085/",
        actor="local-assurance-service",
    )


def _event(source_digit: str = "3") -> ReplayEvent:
    (
        payload_sha256,
        source_event_id,
        idempotency_key,
        replay_observed_at,
        metric_name,
        metric_value,
        scenario,
        source_observation_digit,
    ) = EVENT_IDENTITIES[source_digit]
    return ReplayEvent(
        schema_version="1.0",
        sequence_number=1,
        source_event_id=source_event_id,
        idempotency_key=idempotency_key,
        dataset_id="bubbleran-persistent-interference",
        dataset_version="fa4e3333855d64474e710bc5bebf11a9ec075e0b",
        scenario=scenario,
        lock_id="lablock-" + "1" * 64,
        bundle_id="labbundle-" + "2" * 64,
        source_observation_id="labobs-" + source_observation_digit * 64,
        source_observed_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
        replay_observed_at=replay_observed_at,
        scheduled_offset_seconds=0.0,
        resource_id="lab:5g-sa:gnb:0123456789abcdef01234567",
        technology="5G_SA",
        metrics={metric_name: metric_value},
        units={metric_name: "ratio"},
        quality_flags=(),
        payload_sha256=payload_sha256,
    )


def _wire(source_digit: str = "3") -> tuple[ReplayWirePayload, bytes]:
    event = _event(source_digit)
    sink_payload = event.sink_payload()
    body = canonical_json_bytes(sink_payload)
    wire = validate_replay_wire_payload(json.loads(body))
    assert isinstance(wire, ReplayWirePayload)
    assert canonical_json_bytes(wire.to_sink_payload()) == body
    return wire, body


def _headers(wire: ReplayWirePayload) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": wire.idempotency_key,
        "X-NetworkAgent-Local-Operation": "replay-v1",
    }


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


def test_real_wire_missing_ul_bler_is_durable_discoverable_and_non_actionable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    wire, body = _wire()

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
            assert response.status_code == 202
            receipt = response.json()
            assert receipt == {
                "ok": True,
                "data": {
                    "accepted": "DURABLE",
                    "source_event_id": wire.source_event_id,
                    "payload_sha256": wire.payload_sha256,
                    "incident_id": receipt["data"]["incident_id"],
                    "status": "DETECTED",
                    "revision": 0,
                    "technology": "5G_SA",
                    "scope": [
                        {
                            "resource_id": wire.resource_id,
                            "resource_type": "GNB",
                            "technology": "5G_SA",
                        }
                    ],
                },
            }
            incident_id = receipt["data"]["incident_id"]

            associations = await (
                app.state.assurance_components.profile.incident_repository
            ).source_event_associations(incident_id, limit=1)
            assert len(associations) == 1
            assert associations[0].source_event_id == wire.source_event_id
            assert associations[0].idempotency_key == wire.idempotency_key

            discovered = await client.get(f"/local/v1/incidents/{incident_id}")
            assert discovered.status_code == 200
            assert discovered.json()["data"]["incident"] == {
                "incident_id": incident_id,
                "status": "DETECTED",
                "severity": "UNKNOWN",
                "technology": "5G_SA",
                "title": "Validated local replay KPI fault",
                "revision": 0,
                "scope": [
                    {
                        "resource_id": wire.resource_id,
                        "resource_type": "GNB",
                        "technology": "5G_SA",
                        "parent_resource_id": None,
                    }
                ],
            }

            incident = await (
                app.state.assurance_components.profile.incident_repository
            ).get(incident_id)
            assert incident is not None
            assert incident.violated_kpis == ()
            assert incident.rule_versions == {}
            assert "detector_algorithm" not in incident.model_metadata
            assert incident.recommendations == ()
            assert incident.action_runs == ()

    asyncio.run(scenario())


def test_positive_rule_governance_resolves_and_receiver_replay_is_byte_stable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    wire, body = _wire("5")

    async def scenario() -> None:
        first_app = create_app(config, clock=lambda: NOW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://127.0.0.1",
        ) as client:
            first = await client.post(
                "/local/v1/faults/replay", headers=_headers(wire), content=body
            )
            assert first.status_code == 202
            first_body = first.content
            assert _database_counts(config.database_path) == (1, 1, 1, 1)

            incident_id = first.json()["data"]["incident_id"]
            repository = (
                first_app.state.assurance_components.profile.incident_repository
            )
            candidate = await repository.get(incident_id)
            assert candidate is not None
            assert len(candidate.violated_kpis) == 1
            violation = candidate.violated_kpis[0]
            rule = (
                first_app.state.assurance_components.profile.rule_repository
            ).get_version(BUBBLERAN_REPLAY_RULE_ID, "1.0.0")
            assert rule is not None
            assert {
                "kpi_name": violation.kpi_name,
                "observed_value": violation.observed_value,
                "threshold_value": violation.threshold_value,
                "comparator": violation.comparator.value,
                "unit": violation.unit,
                "rule_id": violation.rule_id,
                "rule_version": violation.rule_version,
                "resource_ids": violation.resource_ids,
            } == {
                "kpi_name": rule.detection.kpi_name,
                "observed_value": 0.2,
                "threshold_value": rule.detection.threshold,
                "comparator": rule.detection.comparator.value,
                "unit": rule.detection.unit,
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "resource_ids": (wire.resource_id,),
            }
            assert candidate.rule_versions == {rule.rule_id: rule.version}
            assert candidate.model_metadata["detector_algorithm"] == (
                BUBBLERAN_REPLAY_DETECTOR_ALGORITHM
            )
            assert candidate.model_metadata["rule_content_hashes"] == {
                rule.rule_id: rule_content_sha256(rule)
            }

            prepared = await client.post(
                f"/local/v1/incidents/{incident_id}/prepare",
                headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
                json={
                    "idempotency_key": "prepare-positive-5g-replay",
                    "actor": "local-governance",
                },
            )
            assert prepared.status_code == 200
            prepared_data = prepared.json()["data"]
            assert prepared_data["incident"]["status"] == "AWAITING_APPROVAL"
            assert prepared_data["rca"]["conclusion"] == "CONCLUSIVE"
            assert prepared_data["action"]["action_type"] == "LOCAL_SIMULATION"
            assert prepared_data["approval"]["status"] == "PENDING"

            decided = await client.post(
                f"/local/v1/incidents/{incident_id}/decide",
                headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
                json={
                    "idempotency_key": "decide-positive-5g-replay",
                    "actor": "local-operator",
                    "reason": "approve the exact isolated local simulation",
                    "approve": True,
                    "expected_action_hash": prepared_data["action"]["action_hash"],
                    "expected_revision": prepared_data["incident"]["revision"],
                },
            )
            assert decided.status_code == 200
            assert decided.json()["data"]["incident"]["status"] == "REMEDIATING"

            executed = await client.post(
                f"/local/v1/incidents/{incident_id}/execute",
                headers={"X-NetworkAgent-Local-Operation": "governance-v1"},
                json={
                    "idempotency_key": "execute-positive-5g-replay",
                    "actor": "local-operator",
                    "verification_passed": True,
                },
            )
            assert executed.status_code == 200
            assert executed.json()["data"]["incident"]["status"] == "RESOLVED"
            assert executed.json()["data"]["verification"]["status"] == "PASSED"

        repository = first_app.state.assurance_components.profile.incident_repository
        current = await repository.get(incident_id)
        assert current is not None
        assert current.status is IncidentStatus.RESOLVED
        before_replay = _database_counts(config.database_path)

        restarted_app = create_app(config, clock=lambda: NOW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted_app),
            base_url="http://127.0.0.1",
        ) as client:
            replay = await client.post(
                "/local/v1/faults/replay", headers=_headers(wire), content=body
            )
        assert replay.status_code == 202
        assert replay.content == first_body
        assert _database_counts(config.database_path) == before_replay

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ("incident", "audit"))
def test_replay_fails_closed_when_the_durable_ingest_snapshot_is_incomplete(
    tmp_path: Path,
    corruption: str,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    wire, body = _wire("5")

    async def first_delivery() -> str:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
        assert response.status_code == 202
        return str(response.json()["data"]["incident_id"])

    incident_id = asyncio.run(first_delivery())
    connection = duckdb.connect(str(config.database_path))
    try:
        table = (
            "canonical_incidents"
            if corruption == "incident"
            else "canonical_incident_audit"
        )
        connection.execute(
            f"DELETE FROM {table} WHERE incident_id = ?",
            [incident_id],
        )
    finally:
        connection.close()
    counts_after_corruption = _database_counts(config.database_path)

    async def replay() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert response.json()["error"]["code"] == "LOCAL_FAULT_UNAVAILABLE"
        assert incident_id not in response.text
        assert wire.source_event_id not in response.text

    asyncio.run(replay())
    assert _database_counts(config.database_path) == counts_after_corruption


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_code"),
    (
        ("missing_operation", 403, "LOCAL_FAULT_OPERATION_REQUIRED"),
        ("missing_idempotency", 403, "LOCAL_FAULT_IDEMPOTENCY_REQUIRED"),
        ("mismatched_idempotency", 409, "LOCAL_FAULT_IDEMPOTENCY_CONFLICT"),
        ("bad_host", 403, "LOCAL_FAULT_BAD_HOST"),
        ("bad_peer", 403, "LOCAL_FAULT_BAD_HOST"),
        ("wrong_media_type", 415, "LOCAL_FAULT_UNSUPPORTED_MEDIA_TYPE"),
        ("extra_label", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("string_metric", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("bad_checksum", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("wrong_scenario", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("duplicate_key", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("lone_surrogate", 422, "LOCAL_FAULT_INVALID_REQUEST"),
        ("query", 422, "LOCAL_FAULT_INVALID_REQUEST"),
    ),
)
def test_boundary_failures_are_fixed_and_make_zero_writes(
    tmp_path: Path,
    mutation: str,
    expected_status: int,
    expected_code: str,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    wire, body = _wire("wrong-scenario" if mutation == "wrong_scenario" else "3")
    headers = _headers(wire)
    target = "/local/v1/faults/replay"
    transport = httpx.ASGITransport(app=app)

    if mutation == "missing_operation":
        headers.pop("X-NetworkAgent-Local-Operation")
    elif mutation == "missing_idempotency":
        headers.pop("Idempotency-Key")
    elif mutation == "mismatched_idempotency":
        headers["Idempotency-Key"] = "labidempotency-" + "f" * 64
    elif mutation == "bad_host":
        headers["Host"] = "example.invalid"
    elif mutation == "bad_peer":
        transport = httpx.ASGITransport(app=app, client=("203.0.113.8", 4321))
    elif mutation == "wrong_media_type":
        headers["Content-Type"] = "text/plain"
    elif mutation in {"extra_label", "string_metric", "bad_checksum"}:
        payload = wire.to_sink_payload()
        if mutation == "extra_label":
            payload["label"] = "persistent_interference"
        elif mutation == "string_metric":
            payload["metrics"] = {"ran.mac.dl_bler": "0.125"}
        else:
            payload["payload_sha256"] = "0" * 64
        body = canonical_json_bytes(payload)
    elif mutation == "duplicate_key":
        rendered = body.decode("utf-8")
        body = rendered.replace(
            '"schema_version":"1.0",',
            '"schema_version":"1.0","schema_version":"1.0",',
        ).encode("utf-8")
    elif mutation == "lone_surrogate":
        payload = wire.to_sink_payload()
        rendered = canonical_json_bytes(payload).decode("utf-8")
        body = rendered.replace(
            '"scenario":"bubbleran-persistent-interference"',
            '"scenario":"\\ud800"',
        ).encode("utf-8")
    elif mutation == "query":
        target += "?source=unsafe"

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(target, headers=headers, content=body)
        assert response.status_code == expected_status
        assert response.json() == {
            "ok": False,
            "error": {
                "code": expected_code,
                "message": response.json()["error"]["message"],
            },
        }
        rendered = response.text
        assert str(config.database_path) not in rendered
        assert "persistent_interference" not in rendered

    asyncio.run(scenario())
    assert _database_counts(config.database_path) == (0, 0, 0, 0)


def test_fault_route_rejects_every_standard_wrong_method_as_fixed_json(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            for method in (
                "GET",
                "HEAD",
                "PUT",
                "PATCH",
                "DELETE",
                "OPTIONS",
                "TRACE",
                "CONNECT",
            ):
                response = await client.request(method, "/local/v1/faults/replay")
                assert response.status_code == 405
                assert response.headers["content-type"] == "application/json"
                assert response.headers["allow"] == "POST"
                if method == "HEAD":
                    assert response.content == b""
                else:
                    assert response.json() == {
                        "ok": False,
                        "error": {
                            "code": "LOCAL_FAULT_METHOD_NOT_ALLOWED",
                            "message": "The HTTP method is not supported for the replay route.",
                        },
                    }

    asyncio.run(scenario())
    assert _database_counts(config.database_path) == (0, 0, 0, 0)


def test_fault_timeout_keeps_unknown_commit_inflight_and_exact_retry_recovers(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    profile = initialize_assurance(config, clock=lambda: NOW)
    durable_receiver = LocalReplayFaultReceiver(
        profile.incident_repository,
        profile.rule_repository,
        actor=config.actor,
    )
    durable_write_finished = threading.Event()
    release_receipt = threading.Event()

    class _DelayedReceiptReceiver:
        writes = 0
        delayed = False

        async def ingest(self, wire: ReplayWirePayload):
            before = _database_counts(config.database_path)
            receipt = await durable_receiver.ingest(wire)
            after = _database_counts(config.database_path)
            if after != before:
                self.writes += 1
            if not self.delayed:
                self.delayed = True
                durable_write_finished.set()
                if not release_receipt.wait(timeout=2.0):
                    raise RuntimeError("test receipt release timed out")
            return receipt

    receiver = _DelayedReceiptReceiver()
    boundary = LocalBusinessOperationBoundary(deadline_seconds=1.0)
    app = Starlette(
        routes=[
            *fault_receiver_routes(
                receiver,
                operation_boundary=boundary,
            )
        ]
    )
    wire, body = _wire("5")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            started = time.monotonic()
            uncertain = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
            elapsed = time.monotonic() - started
            assert elapsed < 1.5
            assert uncertain.status_code == 503
            assert uncertain.json()["error"] == {
                "code": "LOCAL_FAULT_OPERATION_UNCERTAIN",
                "message": "The replay operation is still completing.",
            }
            assert durable_write_finished.is_set()

            busy_body_touched = False

            async def busy_body_that_must_not_be_read():
                nonlocal busy_body_touched
                busy_body_touched = True
                yield body

            busy = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=busy_body_that_must_not_be_read(),
            )
            assert busy.status_code == 503
            assert busy.headers["connection"] == "close"
            assert busy.json()["error"] == {
                "code": "LOCAL_FAULT_OPERATION_BUSY",
                "message": "The replay operation worker is busy.",
            }
            assert not busy_body_touched

            release_receipt.set()
            assert await asyncio.to_thread(boundary.wait_until_idle, 1.0)
            recovered = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
            assert recovered.status_code == 202
            assert recovered.json()["data"]["source_event_id"] == wire.source_event_id

    asyncio.run(scenario())
    assert receiver.writes == 1
    assert _database_counts(config.database_path) == (1, 1, 1, 1)


def test_fault_slow_body_timeout_is_fixed_and_never_submits_business_work(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)

    class _UnexpectedReceiver:
        calls = 0

        async def ingest(self, _wire: ReplayWirePayload):
            self.calls += 1
            raise AssertionError("a timed-out body must not reach the receiver")

    receiver = _UnexpectedReceiver()
    operation_boundary = LocalBusinessOperationBoundary(deadline_seconds=1.0)
    admission = LocalHttpRequestAdmission(body_deadline_seconds=0.05)
    app = Starlette(
        routes=[
            *fault_receiver_routes(
                receiver,
                operation_boundary=operation_boundary,
                request_admission=admission,
            )
        ]
    )
    wire, _ = _wire("5")

    async def scenario() -> None:
        cancelled = asyncio.Event()

        async def slow_body():
            try:
                yield b'{"schema_version":"1.0"'
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=slow_body(),
            )
            assert response.status_code == 408
            assert response.headers["connection"] == "close"
            assert response.json() == {
                "ok": False,
                "error": {
                    "code": "LOCAL_FAULT_REQUEST_TIMEOUT",
                    "message": "The replay request body timed out.",
                },
            }
            assert cancelled.is_set()
            assert receiver.calls == 0
            assert not operation_boundary.worker_is_alive
            assert not admission.is_busy

    asyncio.run(scenario())
    assert operation_boundary.close()
    assert _database_counts(config.database_path) == (0, 0, 0, 0)


def test_fault_disconnect_releases_admission_without_business_call(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)

    class _UnexpectedReceiver:
        calls = 0

        async def ingest(self, _wire: ReplayWirePayload):
            self.calls += 1
            raise AssertionError("a disconnected body must not reach the receiver")

    receiver = _UnexpectedReceiver()
    operation_boundary = LocalBusinessOperationBoundary(deadline_seconds=1.0)
    admission = LocalHttpRequestAdmission(body_deadline_seconds=1.0)
    app = Starlette(
        routes=[
            *fault_receiver_routes(
                receiver,
                operation_boundary=operation_boundary,
                request_admission=admission,
            )
        ]
    )
    wire, _ = _wire("5")

    async def scenario() -> None:
        async def disconnected_body():
            yield b"{"
            raise ClientDisconnect()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=disconnected_body(),
            )
            assert response.status_code == 422
            assert response.headers["connection"] == "close"
            assert response.json()["error"]["code"] == "LOCAL_FAULT_INVALID_REQUEST"
            assert receiver.calls == 0
            assert not operation_boundary.worker_is_alive
            assert not admission.is_busy

    asyncio.run(scenario())
    assert operation_boundary.close()
    assert _database_counts(config.database_path) == (0, 0, 0, 0)


def test_governance_and_fault_share_zero_queue_admission_before_body_read(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    operation_boundary = app.state.local_business_operation_boundary
    admission = app.state.local_http_request_admission
    admission.body_deadline_seconds = 1.0
    wire, body = _wire("5")

    async def scenario() -> None:
        first_stream_started = asyncio.Event()
        release_first_stream = asyncio.Event()
        second_stream_touched = False

        async def held_governance_body():
            first_stream_started.set()
            await release_first_stream.wait()
            yield json.dumps(
                {"idempotency_key": "prepare-once", "actor": "operator"},
                separators=(",", ":"),
            ).encode("utf-8")

        async def fault_body_that_must_not_be_read():
            nonlocal second_stream_touched
            second_stream_touched = True
            yield body

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/local/v1/incidents/admission-held/prepare",
                    headers={
                        "X-NetworkAgent-Local-Operation": "governance-v1",
                        "Content-Type": "application/json",
                    },
                    content=held_governance_body(),
                )
            )
            await asyncio.wait_for(first_stream_started.wait(), timeout=0.5)
            assert admission.is_busy
            assert not operation_boundary.worker_is_alive

            busy = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=fault_body_that_must_not_be_read(),
            )
            assert busy.status_code == 503
            assert busy.headers["connection"] == "close"
            assert busy.json() == {
                "ok": False,
                "error": {
                    "code": "LOCAL_FAULT_OPERATION_BUSY",
                    "message": "The replay operation worker is busy.",
                },
            }
            assert not second_stream_touched
            assert not operation_boundary.worker_is_alive
            assert _database_counts(config.database_path) == (0, 0, 0, 0)

            release_first_stream.set()
            first_response = await first
            assert first_response.status_code == 404
            assert first_response.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_NOT_FOUND"
            )
            assert not admission.is_busy

    asyncio.run(scenario())
    assert operation_boundary.close()
    assert _database_counts(config.database_path) == (0, 0, 0, 0)


def test_governance_and_fault_share_one_zero_queue_business_worker(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    worker_started = threading.Event()
    release_worker = threading.Event()

    class _BlockingGovernanceRepository:
        calls = 0

        async def get(self, _incident_id: str):
            self.calls += 1
            worker_started.set()
            if not release_worker.wait(timeout=2.0):
                raise RuntimeError("test worker release timed out")
            return None

    repository = _BlockingGovernanceRepository()
    app.state.local_governance_engine.repository = repository
    wire, body = _wire("5")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            governance = asyncio.create_task(client.get("/local/v1/incidents/blocked"))
            assert await asyncio.to_thread(worker_started.wait, 1.0)

            fault = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
            assert fault.status_code == 503
            assert fault.json()["error"]["code"] == "LOCAL_FAULT_OPERATION_BUSY"
            assert repository.calls == 1
            assert _database_counts(config.database_path) == (0, 0, 0, 0)

            release_worker.set()
            completed = await governance
            assert completed.status_code == 404

    asyncio.run(scenario())


def test_valid_wire_changed_under_same_idempotency_conflicts_without_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    wire, body = _wire()

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            first = await client.post(
                "/local/v1/faults/replay", headers=_headers(wire), content=body
            )
            assert first.status_code == 202

            changed_payload = wire.to_sink_payload()
            changed_payload["bundle_id"] = "labbundle-" + "9" * 64
            changed_wire = validate_replay_wire_payload(
                json.loads(canonical_json_bytes(changed_payload))
            )
            assert changed_wire.request_fingerprint_sha256 != (
                wire.request_fingerprint_sha256
            )
            conflict = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=canonical_json_bytes(changed_payload),
            )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == ("LOCAL_FAULT_IDEMPOTENCY_CONFLICT")

    asyncio.run(scenario())
    assert _database_counts(config.database_path) == (1, 1, 1, 1)


def test_distinct_sources_remain_independent_when_delivered_out_of_order(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    low_wire, low_body = _wire("3")
    high_wire, high_body = _wire("4")
    repository = app.state.assurance_components.profile.incident_repository

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            high = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(high_wire),
                content=high_body,
            )
            low = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(low_wire),
                content=low_body,
            )
            replayed_low = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(low_wire),
                content=low_body,
            )
        assert high.status_code == low.status_code == replayed_low.status_code == 202
        high_id = high.json()["data"]["incident_id"]
        low_id = low.json()["data"]["incident_id"]
        assert high_id != low_id
        assert replayed_low.content == low.content
        assert _database_counts(config.database_path) == (2, 2, 2, 2)

        high_incident = await repository.get(high_id)
        low_incident = await repository.get(low_id)
        assert high_incident is not None and low_incident is not None
        assert high_incident.source_event_ids == (high_wire.source_event_id,)
        assert low_incident.source_event_ids == (low_wire.source_event_id,)
        assert high_incident.correlation_key != low_incident.correlation_key
        assert high_incident.model_metadata["replay_metrics"] == {
            "ran.mac.dl_bler": 0.875
        }
        assert low_incident.model_metadata["replay_metrics"] == {
            "ran.mac.dl_bler": 0.125
        }

    asyncio.run(scenario())


def test_ul_bler_at_threshold_does_not_create_a_rule_violation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    wire, body = _wire("6")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
        assert response.status_code == 202
        incident = await app.state.assurance_components.profile.incident_repository.get(
            response.json()["data"]["incident_id"]
        )
        assert incident is not None
        assert incident.violated_kpis == ()
        assert incident.rule_versions == {}
        assert "detector_algorithm" not in incident.model_metadata
        assert "rule_content_hashes" not in incident.model_metadata

    asyncio.run(scenario())


def test_rule_drift_fails_before_any_incident_repository_write(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    profile = initialize_assurance(config, clock=lambda: NOW)
    exact = profile.rule_repository.get_version(
        BUBBLERAN_REPLAY_RULE_ID,
        "1.0.0",
    )
    assert exact is not None
    drift_payload = exact.model_dump(mode="python", round_trip=True)
    drift_payload["detection"] = {
        **dict(drift_payload["detection"]),
        "threshold": 0.2,
    }
    drifted = RcaRule.model_validate(drift_payload)

    class DriftRuleRepository:
        def get_version(self, rule_id: str, version: str):
            assert rule_id == BUBBLERAN_REPLAY_RULE_ID
            assert version == "1.0.0"
            return drifted

    class CountingIncidentRepository:
        calls = 0

        async def create_or_correlate(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("must not write with a drifted rule")

    repository = CountingIncidentRepository()
    receiver = LocalReplayFaultReceiver(
        repository,
        DriftRuleRepository(),
        actor="local-assurance-service",
    )
    app = Starlette(routes=[*fault_receiver_routes(receiver)])
    wire, body = _wire("5")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay",
                headers=_headers(wire),
                content=body,
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LOCAL_FAULT_UNAVAILABLE"
        assert repository.calls == 0

    asyncio.run(scenario())


def test_dependency_failure_is_retryable_and_never_acknowledged(tmp_path: Path) -> None:
    config = _config(tmp_path)
    profile = initialize_assurance(config, clock=lambda: NOW)
    repository = config.local_profile_config

    class BrokenRepository:
        async def create_or_correlate(self, *args, **kwargs):
            raise RuntimeError("private dependency path")

    receiver = LocalReplayFaultReceiver(
        BrokenRepository(),
        profile.rule_repository,
        actor="local-assurance-service",
    )
    app = Starlette(routes=[*fault_receiver_routes(receiver)])
    wire, body = _wire()

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(
                "/local/v1/faults/replay", headers=_headers(wire), content=body
            )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert response.json()["error"] == {
            "code": "LOCAL_FAULT_UNAVAILABLE",
            "message": "The local fault receiver is temporarily unavailable.",
        }
        assert "private dependency path" not in response.text

    asyncio.run(scenario())
    assert repository.database_path == config.database_path
    assert _database_counts(config.database_path) == (0, 0, 0, 0)
