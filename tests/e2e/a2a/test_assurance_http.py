"""Real A2A 0.3.11 ASGI/HTTP acceptance tests for the P2b slice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from starlette.applications import Starlette
from telco_assurance_agent.app import create_app, initialize_assurance
from telco_assurance_agent.config import AssuranceConfig
from telco_assurance_agent.service import AssuranceInterruption
from telco_domain import RcaResult


ROOT = Path(__file__).resolve().parents[3]


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


@dataclass(frozen=True)
class Harness:
    app: Starlette
    config: AssuranceConfig
    clock: MutableClock


def _harness(
    tmp_path: Path,
    *,
    after_incident_write: Callable[[object], object] | None = None,
) -> Harness:
    clock = MutableClock(datetime(2030, 1, 1, tzinfo=UTC))
    config = AssuranceConfig(
        database_path=tmp_path / "assurance-http.duckdb",
        performance_csv_path=ROOT / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=ROOT / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=ROOT / "data/rca-rules/lte",
        documents_dir=ROOT / "data/docs/lte",
        public_url="http://127.0.0.1:8085/",
        actor="supervisor:user",
        source_timezone="UTC",
        challenge_ttl_seconds=60,
    )
    initialize_assurance(config, reset=True, clock=clock)
    return Harness(
        app=create_app(
            config,
            clock=clock,
            after_incident_write=after_incident_write,
        ),
        config=config,
        clock=clock,
    )


def _common(label: str, message_type: str, now: datetime) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "message_type": message_type,
        "message_id": f"message-{label}",
        "workflow_id": f"workflow-{label}",
        "trace_id": f"trace-{label}",
        "idempotency_key": f"idempotency-{label}",
        "sent_at": now.isoformat().replace("+00:00", "Z"),
    }


def _scan(label: str, now: datetime) -> dict[str, Any]:
    return {
        **_common(label, "assurance_scan_request", now),
        "window_start": None,
        "window_end": None,
        "resource_ids": [],
        "page_size": 1,
        "page_offset": 0,
    }


def _confirmation(
    label: str,
    page: dict[str, Any],
    now: datetime,
    *,
    decision: str,
) -> dict[str, Any]:
    return {
        **_common(label, "assurance_confirmation_request", now),
        "workflow_id": page["workflow_id"],
        "trace_id": page["trace_id"],
        "preview_message_id": page["message_id"],
        "candidate_id": page["candidates"][0]["candidate_id"],
        "challenge_id": page["challenge_id"],
        "snapshot_sha256": page["snapshot_sha256"],
        "decision": decision,
        "reason": "Operator explicitly reviewed the structured candidate.",
    }


def _wire_message(
    data: dict[str, Any] | None,
    *,
    context_id: str,
    task_id: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append({"kind": "text", "text": text})
    if data is not None:
        parts.append({"kind": "data", "data": data})
    message = {
        "role": "user",
        "messageId": data["message_id"] if data else f"text-{context_id}",
        "contextId": context_id,
        "parts": parts,
    }
    if task_id is not None:
        message["taskId"] = task_id
    return message


async def _stream(
    client: httpx.AsyncClient,
    message: dict[str, Any],
    *,
    rpc_id: str,
) -> list[dict[str, Any]]:
    response = await client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "message/stream",
            "params": {"message": message},
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    envelopes = [
        json.loads(line.removeprefix("data:").strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert envelopes
    assert all(envelope.get("jsonrpc") == "2.0" for envelope in envelopes)
    assert not any("error" in envelope for envelope in envelopes)
    return [envelope["result"] for envelope in envelopes]


def _task(results: list[dict[str, Any]]) -> dict[str, Any]:
    return next(result for result in results if result.get("kind") == "task")


def _status(results: list[dict[str, Any]], state: str) -> dict[str, Any]:
    return next(
        result
        for result in results
        if result.get("kind") == "status-update"
        and result["status"]["state"] == state
    )


def _artifact_data(results: list[dict[str, Any]]) -> dict[str, Any]:
    artifact = next(
        result for result in results if result.get("kind") == "artifact-update"
    )
    return next(
        part["data"]
        for part in artifact["artifact"]["parts"]
        if part["kind"] == "data"
    )


def _status_data(status: dict[str, Any]) -> dict[str, Any]:
    return next(
        part["data"]
        for part in status["status"]["message"]["parts"]
        if part["kind"] == "data"
    )


async def _scan_page(
    client: httpx.AsyncClient,
    clock: MutableClock,
    label: str,
    context_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    scan = _scan(label, clock())
    results = await _stream(
        client,
        _wire_message(
            scan,
            context_id=context_id,
            text="Scan the approved Local LTE data.",
        ),
        rpc_id=f"rpc-{label}",
    )
    task = _task(results)
    interrupted = _status(results, "input-required")
    page = _status_data(interrupted)
    assert task["contextId"] == context_id
    assert interrupted["taskId"] == task["id"]
    assert interrupted["final"] is True
    return task, page, results


async def _incident_count(app: Starlette) -> int:
    incidents = await app.state.assurance_components.profile.incident_repository.list()
    return len(incidents)


@pytest.mark.asyncio
async def test_card_detect_confirm_and_read_only_analyze_over_real_http(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    transport = httpx.ASGITransport(app=harness.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://assurance.test"
    ) as client:
        card_response = await client.get("/.well-known/agent-card.json")
        assert card_response.status_code == 200
        card = card_response.json()
        assert card["name"] == "Local Assurance Agent"
        assert card["url"] == "http://127.0.0.1:8085/"
        assert card["capabilities"]["streaming"] is True

        task, page, scan_results = await _scan_page(
            client, harness.clock, "scan-main", "context-main"
        )
        assert _status(scan_results, "working")["final"] is False
        assert page["message_type"] == "assurance_candidate_page"
        assert page["total_candidates"] == 15
        assert len(page["candidates"]) == 1
        assert await _incident_count(harness.app) == 0

        confirmation = _confirmation(
            "confirm-main", page, harness.clock(), decision="CONFIRM"
        )
        confirm_results = await _stream(
            client,
            _wire_message(
                confirmation,
                context_id=task["contextId"],
                task_id=task["id"],
                text="Confirm the exact structured preview.",
            ),
            rpc_id="rpc-confirm-main",
        )
        confirmed = _artifact_data(confirm_results)
        assert confirmed["message_type"] == "assurance_confirmation_result"
        assert confirmed["outcome"] == "created"
        assert _status(confirm_results, "completed")["final"] is True
        assert await _incident_count(harness.app) == 1
        incident_id = confirmed["incident"]["incident_id"]
        repository = harness.app.state.assurance_components.profile.incident_repository
        before_history = await repository.history(incident_id)

        analyze = {
            **_common(
                "analyze-main", "assurance_analyze_request", harness.clock()
            ),
            "trace_id": confirmed["incident"]["trace_id"],
            "incident_id": incident_id,
            "requested_report_version": 1,
        }
        analyze_results = await _stream(
            client,
            _wire_message(
                analyze,
                context_id="context-analyze",
                text="Run deterministic read-only RCA.",
            ),
            rpc_id="rpc-analyze-main",
        )
        rca_data = _artifact_data(analyze_results)
        rca = RcaResult.model_validate(rca_data)
        assert rca.message_type == "rca_result"
        assert rca.incident_id == incident_id
        assert _status(analyze_results, "completed")["final"] is True
        assert await _incident_count(harness.app) == 1
        assert await repository.history(incident_id) == before_history


@pytest.mark.asyncio
async def test_reject_cancel_expired_text_only_and_tamper_are_zero_write(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    transport = httpx.ASGITransport(app=harness.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://assurance.test"
    ) as client:
        text_results = await _stream(
            client,
            _wire_message(
                None,
                context_id="context-text-only",
                text="confirm everything",
            ),
            rpc_id="rpc-text-only",
        )
        text_error = _status_data(_status(text_results, "failed"))
        assert text_error["error_code"] == "ASSURANCE_PROTOCOL_INVALID"
        assert await _incident_count(harness.app) == 0

        reject_task, reject_page, _ = await _scan_page(
            client, harness.clock, "scan-reject", "context-reject"
        )
        rejection = _confirmation(
            "confirm-reject", reject_page, harness.clock(), decision="REJECT"
        )
        reject_results = await _stream(
            client,
            _wire_message(
                rejection,
                context_id=reject_task["contextId"],
                task_id=reject_task["id"],
            ),
            rpc_id="rpc-confirm-reject",
        )
        assert _artifact_data(reject_results)["outcome"] == "rejected"
        assert await _incident_count(harness.app) == 0

        cancel_task, cancel_page, _ = await _scan_page(
            client, harness.clock, "scan-cancel", "context-cancel"
        )
        cancel_response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-cancel",
                "method": "tasks/cancel",
                "params": {"id": cancel_task["id"]},
            },
        )
        assert cancel_response.status_code == 200
        cancelled = cancel_response.json()["result"]
        assert cancelled["status"]["state"] == "canceled"
        pending = await harness.app.state.assurance_pending_store.get(
            cancel_page["message_id"]
        )
        assert pending is not None and pending.state == "cancelled"
        assert await _incident_count(harness.app) == 0

        tamper_task, tamper_page, _ = await _scan_page(
            client, harness.clock, "scan-tamper", "context-tamper"
        )
        tampered = _confirmation(
            "confirm-tamper", tamper_page, harness.clock(), decision="CONFIRM"
        )
        tampered["challenge_id"] = "tampered-" + "x" * 32
        tamper_results = await _stream(
            client,
            _wire_message(
                tampered,
                context_id=tamper_task["contextId"],
                task_id=tamper_task["id"],
            ),
            rpc_id="rpc-confirm-tamper",
        )
        assert (
            _status_data(_status(tamper_results, "failed"))["error_code"]
            == "ASSURANCE_CONFIRMATION_INVALID"
        )
        assert await _incident_count(harness.app) == 0

        expired_task, expired_page, _ = await _scan_page(
            client, harness.clock, "scan-expired", "context-expired"
        )
        harness.clock.advance(seconds=61)
        expired = _confirmation(
            "confirm-expired", expired_page, harness.clock(), decision="CONFIRM"
        )
        expired_results = await _stream(
            client,
            _wire_message(
                expired,
                context_id=expired_task["contextId"],
                task_id=expired_task["id"],
            ),
            rpc_id="rpc-confirm-expired",
        )
        assert (
            _status_data(_status(expired_results, "failed"))["error_code"]
            == "ASSURANCE_CONFIRMATION_EXPIRED"
        )
        assert await _incident_count(harness.app) == 0


@pytest.mark.asyncio
async def test_two_contexts_cannot_swap_confirmations(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    transport = httpx.ASGITransport(app=harness.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://assurance.test"
    ) as client:
        task_a, page_a, _ = await _scan_page(
            client, harness.clock, "scan-a", "context-a"
        )
        task_b, page_b, _ = await _scan_page(
            client, harness.clock, "scan-b", "context-b"
        )
        assert task_a["id"] != task_b["id"]
        assert page_a["workflow_id"] != page_b["workflow_id"]
        assert page_a["trace_id"] != page_b["trace_id"]

        cross_session = _confirmation(
            "confirm-cross", page_b, harness.clock(), decision="CONFIRM"
        )
        cross_results = await _stream(
            client,
            _wire_message(
                cross_session,
                context_id=task_a["contextId"],
                task_id=task_a["id"],
            ),
            rpc_id="rpc-confirm-cross",
        )
        assert (
            _status_data(_status(cross_results, "failed"))["error_code"]
            == "ASSURANCE_CONFIRMATION_INVALID"
        )
        assert await _incident_count(harness.app) == 0

        valid_b = _confirmation(
            "confirm-b", page_b, harness.clock(), decision="CONFIRM"
        )
        valid_results = await _stream(
            client,
            _wire_message(
                valid_b,
                context_id=task_b["contextId"],
                task_id=task_b["id"],
            ),
            rpc_id="rpc-confirm-b",
        )
        assert _artifact_data(valid_results)["outcome"] == "created"
        assert await _incident_count(harness.app) == 1


@pytest.mark.asyncio
async def test_exact_confirmation_replays_after_crash_without_second_write(
    tmp_path: Path,
) -> None:
    crash_once = True

    def interrupt_after_write(_: object) -> None:
        nonlocal crash_once
        if crash_once:
            crash_once = False
            raise AssuranceInterruption("simulated process loss")

    harness = _harness(tmp_path, after_incident_write=interrupt_after_write)
    transport = httpx.ASGITransport(app=harness.app, raise_app_exceptions=True)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://assurance.test"
    ) as client:
        task, page, _ = await _scan_page(
            client, harness.clock, "scan-replay", "context-replay"
        )
        confirmation = _confirmation(
            "confirm-replay", page, harness.clock(), decision="CONFIRM"
        )
        wire = _wire_message(
            confirmation,
            context_id=task["contextId"],
            task_id=task["id"],
        )
        with pytest.raises(AssuranceInterruption):
            await _stream(client, wire, rpc_id="rpc-confirm-crash")
        assert await _incident_count(harness.app) == 1
        incident_id = page["candidates"][0]["candidate_id"]
        original_repository = (
            harness.app.state.assurance_components.profile.incident_repository
        )
        incident_before = await original_repository.get(incident_id)
        history_before = await original_repository.history(incident_id)
        assert incident_before is not None
        assert incident_before.revision == 0
        assert len(history_before) == 1

    restarted = create_app(harness.config, clock=harness.clock)
    restarted_transport = httpx.ASGITransport(app=restarted)
    harness.clock.advance(seconds=1)
    replay_confirmation = {
        **confirmation,
        "message_id": "message-confirm-replay-retry",
        "sent_at": harness.clock().isoformat().replace("+00:00", "Z"),
    }
    replay_wire = _wire_message(
        replay_confirmation,
        context_id=task["contextId"],
        task_id=task["id"],
    )
    async with httpx.AsyncClient(
        transport=restarted_transport, base_url="http://assurance.test"
    ) as client:
        replay_results = await _stream(
            client, replay_wire, rpc_id="rpc-confirm-replay"
        )
        replayed = _artifact_data(replay_results)
        assert replayed["outcome"] == "replayed"
        assert replayed["request_message_id"] == "message-confirm-replay-retry"
        assert replayed["incident"]["incident_id"] == incident_id
        assert _status(replay_results, "completed")["final"] is True
        assert await _incident_count(restarted) == 1
        restarted_repository = (
            restarted.state.assurance_components.profile.incident_repository
        )
        incident_after = await restarted_repository.get(incident_id)
        assert incident_after == incident_before
        assert incident_after is not None and incident_after.revision == 0
        assert await restarted_repository.history(incident_id) == history_before
