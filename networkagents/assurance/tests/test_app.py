from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx
import pytest

from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2025, 11, 30, tzinfo=UTC)


def _config(tmp_path: Path) -> AssuranceConfig:
    return AssuranceConfig(
        database_path=tmp_path / "local.duckdb",
        performance_csv_path=REPOSITORY_ROOT / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=REPOSITORY_ROOT / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=REPOSITORY_ROOT / "data/rca-rules/lte",
        public_url="http://127.0.0.1:8085/",
        actor="local-assurance-service",
    )


def _envelope(message_type: str, *, workflow_id: str, trace_id: str):
    return {
        "schema_version": "1.0",
        "message_type": message_type,
        "message_id": uuid4().hex,
        "workflow_id": workflow_id,
        "trace_id": trace_id,
        "idempotency_key": uuid4().hex,
        "sent_at": NOW.isoformat(),
    }


def _message(data, *, task_id=None, context_id=None, text=None):
    parts = []
    if text is not None:
        parts.append({"kind": "text", "text": text})
    parts.append({"kind": "data", "data": data})
    message = {
        "kind": "message",
        "messageId": data["message_id"],
        "role": "user",
        "parts": parts,
    }
    if task_id is not None:
        message["taskId"] = task_id
    if context_id is not None:
        message["contextId"] = context_id
    return message


def _rpc(method, params, request_id="rpc-1"):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _data(parts):
    return next(part["data"] for part in parts if part["kind"] == "data")


def test_config_rejects_non_loopback_exposure_and_unsafe_urls(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="loopback interface"):
        replace(config, host="0.0.0.0")
    for public_url in (
        "https://assurance.example/",
        "http://user:password@127.0.0.1:8085/",
        "http://127.0.0.1:8085/#fragment",
    ):
        with pytest.raises(ValueError, match="loopback HTTP"):
            replace(config, public_url=public_url)


def test_real_asgi_card_scan_text_gate_and_confirm(
    tmp_path: Path, caplog
) -> None:
    config = _config(tmp_path)
    initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)
    empty_rules = tmp_path / "empty-rules"
    empty_rules.mkdir()
    empty_app = create_app(
        replace(config, rules_dir=empty_rules), clock=lambda: NOW
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://assurance.test"
        ) as client:
            card = (await client.get("/.well-known/agent-card.json")).json()
            assert card["name"] == "Local Assurance Agent"
            assert card["url"] == "http://127.0.0.1:8085/"
            assert {skill["id"] for skill in card["skills"]} == {
                "local_assurance_detect",
                "local_assurance_confirm",
                "local_assurance_analyze",
            }

            sensitive = "IMSI:310410000000001"
            malicious = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": "rpc-malicious",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "kind": "message",
                            "messageId": "message-malicious",
                            "role": "user",
                            "parts": [{"kind": "bogus", "secret": sensitive}],
                        }
                    },
                },
            )
            assert malicious.status_code == 200
            error = malicious.json()["error"]
            assert error == {"code": -32602, "message": "Invalid parameters"}
            assert "data" not in error
            assert sensitive not in malicious.text
            assert sensitive not in caplog.text

            alias_context = "context-alias"
            aliased_scan = {
                **_envelope(
                    "assurance_scan_request",
                    workflow_id=alias_context,
                    trace_id=uuid4().hex,
                ),
                "window_start": None,
                "window_end": None,
                "resource_ids": [],
                "page_size": 1,
                "page_offset": 0,
            }
            aliased = (
                await client.post(
                    "/",
                    json=_rpc(
                        "message/send",
                        {
                            "message": _message(aliased_scan)
                            | {"contextId": alias_context},
                            "configuration": {"blocking": True},
                        },
                        "rpc-alias",
                    ),
                )
            ).json()["result"]
            assert aliased["status"]["state"] == "failed"
            assert (
                await app.state.assurance_components.profile.incident_repository.list()
            ) == ()

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
                "page_size": 1,
                "page_offset": 0,
            }
            response = await client.post(
                "/",
                json=_rpc(
                    "message/send",
                    {"message": _message(scan), "configuration": {"blocking": True}},
                ),
            )
            assert response.status_code == 200
            task = response.json()["result"]
            assert task["status"]["state"] == "input-required"
            page = _data(task["status"]["message"]["parts"])
            assert page["message_type"] == "assurance_candidate_page"
            assert len(page["candidates"]) == 1
            assert "observations" not in page

            confirmation = {
                **_envelope(
                    "assurance_confirmation_request",
                    workflow_id=workflow_id,
                    trace_id=trace_id,
                ),
                "preview_message_id": page["message_id"],
                "candidate_id": page["candidates"][0]["candidate_id"],
                "challenge_id": page["challenge_id"],
                "snapshot_sha256": page["snapshot_sha256"],
                "decision": "CONFIRM",
                "reason": "用户明确确认创建 Incident",
            }
            confirmed_response = await client.post(
                "/",
                json=_rpc(
                    "message/send",
                    {
                        "message": _message(
                            confirmation,
                            task_id=task["id"],
                            context_id=task["contextId"],
                            text="确认创建事件。",
                        ),
                        "configuration": {"blocking": True},
                    },
                    "rpc-2",
                ),
            )
            assert confirmed_response.status_code == 200
            completed = confirmed_response.json()["result"]
            assert completed["status"]["state"] == "completed"
            result = _data(completed["artifacts"][-1]["parts"])
            assert result["message_type"] == "assurance_confirmation_result"
            assert result["outcome"] in {"created", "correlated"}

            gate_workflow, gate_trace = uuid4().hex, uuid4().hex
            gate_scan = {
                **_envelope(
                    "assurance_scan_request",
                    workflow_id=gate_workflow,
                    trace_id=gate_trace,
                ),
                "window_start": None,
                "window_end": None,
                "resource_ids": [],
                "page_size": 1,
                "page_offset": 0,
            }
            gate_task = (
                await client.post(
                    "/",
                    json=_rpc(
                        "message/send",
                        {
                            "message": _message(gate_scan),
                            "configuration": {"blocking": True},
                        },
                        "rpc-3",
                    ),
                )
            ).json()["result"]
            text_only = {
                "kind": "message",
                "messageId": uuid4().hex,
                "taskId": gate_task["id"],
                "contextId": gate_task["contextId"],
                "role": "user",
                "parts": [{"kind": "text", "text": "确认"}],
            }
            gate_response = await client.post(
                "/",
                json=_rpc(
                    "message/send",
                    {
                        "message": text_only,
                        "configuration": {"blocking": True},
                    },
                    "rpc-4",
                ),
            )
            assert gate_response.json()["result"]["status"]["state"] == "failed"

            cancel_workflow, cancel_trace = uuid4().hex, uuid4().hex
            cancel_scan = {
                **_envelope(
                    "assurance_scan_request",
                    workflow_id=cancel_workflow,
                    trace_id=cancel_trace,
                ),
                "window_start": None,
                "window_end": None,
                "resource_ids": [],
                "page_size": 1,
                "page_offset": 0,
            }
            cancel_task = (
                await client.post(
                    "/",
                    json=_rpc(
                        "message/send",
                        {
                            "message": _message(cancel_scan),
                            "configuration": {"blocking": True},
                        },
                        "rpc-5",
                    ),
                )
            ).json()["result"]
            canceled = (
                await client.post(
                    "/",
                    json=_rpc(
                        "tasks/cancel", {"id": cancel_task["id"]}, "rpc-6"
                    ),
                )
            ).json()["result"]
            assert canceled["status"]["state"] == "canceled"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=empty_app),
            base_url="http://assurance.test",
        ) as empty_client:
            empty_workflow, empty_trace = uuid4().hex, uuid4().hex
            empty_scan = {
                **_envelope(
                    "assurance_scan_request",
                    workflow_id=empty_workflow,
                    trace_id=empty_trace,
                ),
                "window_start": None,
                "window_end": None,
                "resource_ids": [],
                "page_size": 1,
                "page_offset": 0,
            }
            empty_task = (
                await empty_client.post(
                    "/",
                    json=_rpc(
                        "message/send",
                        {
                            "message": _message(empty_scan),
                            "configuration": {"blocking": True},
                        },
                        "rpc-empty",
                    ),
                )
            ).json()["result"]
            assert empty_task["status"]["state"] == "completed"
            empty_page = _data(empty_task["artifacts"][-1]["parts"])
            assert empty_page["message_type"] == "assurance_candidate_page"
            assert empty_page["candidates"] == []
            assert empty_page["challenge_id"] is None
            assert empty_page["challenge_expires_at"] is None

    asyncio.run(scenario())
    connection = duckdb.connect(str(config.database_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM canonical_incidents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM assurance_a2a_tasks").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM assurance_pending_confirmations "
            "WHERE state = 'cancelled'"
        ).fetchone()[0] == 1
    finally:
        connection.close()
