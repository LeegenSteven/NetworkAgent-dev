from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx

from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
LOCAL_HEADERS = {
    "X-NetworkAgent-Local-Operation": "governance-v1",
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


def _envelope(message_type: str, *, workflow_id: str, trace_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "message_type": message_type,
        "message_id": uuid4().hex,
        "workflow_id": workflow_id,
        "trace_id": trace_id,
        "idempotency_key": uuid4().hex,
        "sent_at": NOW.isoformat(),
    }


def _message(data: dict, *, task_id: str | None = None, context_id: str | None = None):
    message = {
        "kind": "message",
        "messageId": data["message_id"],
        "role": "user",
        "parts": [{"kind": "data", "data": data}],
    }
    if task_id is not None:
        message["taskId"] = task_id
    if context_id is not None:
        message["contextId"] = context_id
    return message


def _rpc(params: dict, request_id: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "message/send",
        "params": params,
    }


def _data(parts: list[dict]) -> dict:
    return next(part["data"] for part in parts if part["kind"] == "data")


async def _confirm_one_incident(client: httpx.AsyncClient) -> str:
    workflow_id, trace_id = uuid4().hex, uuid4().hex
    scan = {
        **_envelope(
            "assurance_scan_request",
            workflow_id=workflow_id,
            trace_id=trace_id,
        ),
        "window_start": None,
        "window_end": None,
        "resource_ids": [],
        "page_size": 20,
        "page_offset": 0,
    }
    task = (
        await client.post(
            "/",
            json=_rpc(
                {
                    "message": _message(scan),
                    "configuration": {"blocking": True},
                },
                "scan-rpc",
            ),
        )
    ).json()["result"]
    assert task["status"]["state"] == "input-required"
    page = _data(task["status"]["message"]["parts"])

    confirmation = {
        **_envelope(
            "assurance_confirmation_request",
            workflow_id=workflow_id,
            trace_id=trace_id,
        ),
        "preview_message_id": page["message_id"],
        "candidate_id": min(
            candidate["candidate_id"] for candidate in page["candidates"]
        ),
        "challenge_id": page["challenge_id"],
        "snapshot_sha256": page["snapshot_sha256"],
        "decision": "CONFIRM",
        "reason": "用户明确确认创建本地治理事件",
    }
    completed = (
        await client.post(
            "/",
            json=_rpc(
                {
                    "message": _message(
                        confirmation,
                        task_id=task["id"],
                        context_id=task["contextId"],
                    ),
                    "configuration": {"blocking": True},
                },
                "confirm-rpc",
            ),
        )
    ).json()["result"]
    assert completed["status"]["state"] == "completed"
    result = _data(completed["artifacts"][-1]["parts"])
    assert result["outcome"] == "created"
    return result["incident"]["incident_id"]


def _counts(database_path: Path) -> tuple[int, int, int, int, int]:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        row = connection.execute("SELECT payload FROM canonical_incidents").fetchone()
        assert row is not None
        import json

        incident = json.loads(row[0])
        return (
            int(incident["revision"]),
            len(incident["rca_reports"]),
            len(incident["approvals"]),
            len(incident["action_runs"]),
            len(incident["verification_runs"]),
        )
    finally:
        connection.close()


def test_real_asgi_confirm_prepare_decide_execute_and_exact_replay(
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
            card = (await client.get("/.well-known/agent-card.json")).json()
            assert card["name"] == "Local Assurance Agent"

            incident_id = await _confirm_one_incident(client)
            incident_url = f"/local/v1/incidents/{incident_id}"
            initial = await client.get(incident_url)
            assert initial.status_code == 200
            assert initial.json()["data"]["incident"]["status"] == "DETECTED"

            prepare_body = {
                "idempotency_key": "governance-prepare-1",
                "actor": "local-governance",
            }
            prepared = await client.post(
                incident_url + "/prepare",
                headers=LOCAL_HEADERS,
                json=prepare_body,
            )
            assert prepared.status_code == 200
            prepared_data = prepared.json()["data"]
            assert prepared_data["incident"]["status"] == "AWAITING_APPROVAL"
            assert prepared_data["rca"]["conclusion"] == "CONCLUSIVE"
            assert prepared_data["action"]["action_type"] == "LOCAL_SIMULATION"
            assert len(prepared_data["action"]["action_hash"]) == 64
            assert prepared_data["approval"]["status"] == "PENDING"
            assert prepared_data["replayed"] is False

            prepared_replay = await client.post(
                incident_url + "/prepare",
                headers=LOCAL_HEADERS,
                json=prepare_body,
            )
            assert prepared_replay.status_code == 200
            assert prepared_replay.json()["data"]["replayed"] is True
            assert _counts(config.database_path) == (4, 1, 1, 0, 0)

            decide_body = {
                "idempotency_key": "governance-decide-1",
                "actor": "local-operator",
                "reason": "reviewed exact isolated simulation",
                "approve": True,
                "expected_action_hash": prepared_data["action"]["action_hash"],
                "expected_revision": prepared_data["incident"]["revision"],
            }
            decided = await client.post(
                incident_url + "/decide",
                headers=LOCAL_HEADERS,
                json=decide_body,
            )
            assert decided.status_code == 200
            assert decided.json()["data"]["incident"]["status"] == "REMEDIATING"
            assert decided.json()["data"]["approval"]["status"] == "APPROVED"

            decided_replay = await client.post(
                incident_url + "/decide",
                headers=LOCAL_HEADERS,
                json=decide_body,
            )
            assert decided_replay.status_code == 200
            assert decided_replay.json()["data"]["replayed"] is True
            assert _counts(config.database_path) == (5, 1, 2, 0, 0)

            execute_body = {
                "idempotency_key": "governance-execute-1",
                "actor": "local-simulator",
                "verification_passed": True,
            }
            executed = await client.post(
                incident_url + "/execute",
                headers=LOCAL_HEADERS,
                json=execute_body,
            )
            assert executed.status_code == 200
            executed_data = executed.json()["data"]
            assert executed_data["incident"]["status"] == "RESOLVED"
            assert executed_data["action_runs"] == [
                {
                    "action_run_id": executed_data["action_runs"][0]["action_run_id"],
                    "action_hash": prepared_data["action"]["action_hash"],
                    "status": "SUCCEEDED",
                }
            ]
            assert executed_data["verification"]["status"] == "PASSED"

            executed_replay = await client.post(
                incident_url + "/execute",
                headers=LOCAL_HEADERS,
                json=execute_body,
            )
            assert executed_replay.status_code == 200
            assert executed_replay.json()["data"]["replayed"] is True
            assert _counts(config.database_path) == (7, 1, 2, 1, 1)

    asyncio.run(scenario())


def test_governance_boundary_rejects_untrusted_or_changed_requests_without_action(
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
            incident_id = await _confirm_one_incident(client)
            base = f"/local/v1/incidents/{incident_id}"
            prepare_body = {
                "idempotency_key": "governance-prepare-boundary",
                "actor": "local-governance",
            }

            preflight = await client.options(base + "/prepare")
            assert preflight.status_code in {404, 405}
            assert "access-control-allow-origin" not in preflight.headers

            wrong_media = await client.post(
                base + "/prepare",
                headers={**LOCAL_HEADERS, "Content-Type": "text/plain"},
                content="{}",
            )
            assert wrong_media.status_code == 415
            assert wrong_media.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_UNSUPPORTED_MEDIA_TYPE"
            )

            lone_surrogate = await client.post(
                base + "/prepare",
                headers={**LOCAL_HEADERS, "Content-Type": "application/json"},
                content=(
                    b'{"idempotency_key":"surrogate-request",' b'"actor":"\\ud800"}'
                ),
            )
            assert lone_surrogate.status_code == 422
            assert lone_surrogate.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_INVALID_REQUEST"
            )
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            oversized = await client.post(
                base + "/prepare",
                headers={**LOCAL_HEADERS, "Content-Type": "application/json"},
                content=b"{" + b'"padding":"' + b"x" * 65_536 + b'"}',
            )
            assert oversized.status_code == 413
            assert oversized.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_REQUEST_TOO_LARGE"
            )
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            missing = await client.get("/local/v1/incidents/not-present")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "LOCAL_GOVERNANCE_NOT_FOUND"
            assert "not-present" not in missing.text

            for peer in (None, ("203.0.113.9", 43123), ("not-an-ip", 43123)):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, client=peer),
                    base_url="http://127.0.0.1",
                ) as untrusted_peer:
                    rejected_peer = await untrusted_peer.get(base)
                    assert rejected_peer.status_code == 403
                    assert rejected_peer.json()["error"]["code"] == (
                        "LOCAL_GOVERNANCE_BAD_HOST"
                    )
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            missing_header = await client.post(base + "/prepare", json=prepare_body)
            assert missing_header.status_code == 403
            assert missing_header.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_OPERATION_REQUIRED"
            )
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            bad_host = await client.post(
                base + "/prepare",
                headers={**LOCAL_HEADERS, "Host": "assurance.example"},
                json=prepare_body,
            )
            assert bad_host.status_code == 403
            assert bad_host.json()["error"]["code"] == "LOCAL_GOVERNANCE_BAD_HOST"
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            extra = await client.post(
                base + "/prepare",
                headers=LOCAL_HEADERS,
                json={**prepare_body, "unexpected": True},
            )
            assert extra.status_code == 422
            assert extra.json()["error"]["code"] == ("LOCAL_GOVERNANCE_INVALID_REQUEST")
            assert "unexpected" not in extra.text
            assert _counts(config.database_path) == (0, 0, 0, 0, 0)

            prepared = await client.post(
                base + "/prepare", headers=LOCAL_HEADERS, json=prepare_body
            )
            preview = prepared.json()["data"]
            assert preview["incident"]["status"] == "AWAITING_APPROVAL"

            string_bool = await client.post(
                base + "/decide",
                headers=LOCAL_HEADERS,
                json={
                    "idempotency_key": "governance-decide-string-bool",
                    "actor": "local-operator",
                    "reason": "must reject coercion",
                    "approve": "true",
                    "expected_action_hash": preview["action"]["action_hash"],
                    "expected_revision": preview["incident"]["revision"],
                },
            )
            assert string_bool.status_code == 422
            assert string_bool.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_INVALID_REQUEST"
            )

            wrong_hash = await client.post(
                base + "/decide",
                headers=LOCAL_HEADERS,
                json={
                    "idempotency_key": "governance-decide-wrong-hash",
                    "actor": "local-operator",
                    "reason": "wrong immutable binding",
                    "approve": True,
                    "expected_action_hash": "0" * 64,
                    "expected_revision": preview["incident"]["revision"],
                },
            )
            assert wrong_hash.status_code == 403
            assert wrong_hash.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_AUTHORIZATION_FAILED"
            )
            revision, reports, approvals, actions, verifications = _counts(
                config.database_path
            )
            assert (revision, reports, approvals, actions, verifications) == (
                4,
                1,
                1,
                0,
                0,
            )

            original_repository = app.state.local_governance_engine.repository
            current = await original_repository.get(incident_id)
            assert current is not None

            class _OversizedRepository:
                async def get(self, _incident_id: str):
                    return current.model_copy(update={"title": "x" * 300_000})

            app.state.local_governance_engine.repository = _OversizedRepository()
            oversized_response = await client.get(base)
            assert oversized_response.status_code == 500
            assert oversized_response.json()["error"]["code"] == (
                "LOCAL_GOVERNANCE_RESPONSE_TOO_LARGE"
            )
            assert "x" * 1_000 not in oversized_response.text

            class _FailingRepository:
                async def get(self, _incident_id: str):
                    raise RuntimeError("C:/private/runtime.duckdb IMSI:310410000000001")

            app.state.local_governance_engine.repository = _FailingRepository()
            failed = await client.get(base)
            assert failed.status_code == 500
            assert failed.json()["error"]["code"] == "LOCAL_GOVERNANCE_INTERNAL"
            assert "private" not in failed.text
            assert "IMSI" not in failed.text

    asyncio.run(scenario())
