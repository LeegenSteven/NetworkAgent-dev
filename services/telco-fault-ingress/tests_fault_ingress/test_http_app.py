from __future__ import annotations

from dataclasses import replace
import pytest
from aiohttp.test_utils import TestClient, TestServer

from telco_cloud import IngestDisposition, IngestResult
from telco_domain import SourceEventAssociation
from telco_fault_ingress.app import create_app
from telco_fault_ingress.config import FaultPipelineMode

from .conftest import push_body


class Repository:
    def __init__(self, failure: bool = False) -> None:
        self.failure = failure

    async def ingest(self, envelope, **kwargs):
        if self.failure:
            raise RuntimeError("raw-secret-backend-error")
        shadow = kwargs["shadow"]
        association = (
            None
            if shadow
            else SourceEventAssociation(
                incident_id=envelope.incident.incident_id,
                source_event_id=envelope.source_event_id,
                registered_at=envelope.received_at,
                actor=kwargs["actor"],
                reason=kwargs["reason"],
                idempotency_key=kwargs["idempotency_key"],
                trace_id=envelope.trace_id,
            )
        )
        return IngestResult(
            disposition=(
                IngestDisposition.SHADOW_RECORDED
                if shadow
                else IngestDisposition.CREATED
            ),
            source_event_id=envelope.source_event_id,
            trace_id=envelope.trace_id,
            incident=None if shadow else envelope.incident,
            source_association=association,
            outbox_event_id=None if shadow else "outbox-1",
        )


@pytest.mark.asyncio
async def test_durable_event_returns_empty_204(config, fixed_now) -> None:
    async with TestClient(
        TestServer(create_app(config, Repository(), clock=lambda: fixed_now))
    ) as client:
        response = await client.post(
            "/events/pubsub",
            data=push_body(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 204
        assert await response.read() == b""
        assert response.headers.get("Access-Control-Allow-Origin") is None


@pytest.mark.asyncio
async def test_poison_is_400_and_dependency_failure_is_503_without_reflection(
    config, fixed_now, caplog
) -> None:
    raw_marker = "subscriber-secret-marker"
    async with TestClient(
        TestServer(create_app(config, Repository(), clock=lambda: fixed_now))
    ) as client:
        response = await client.post(
            "/events/pubsub",
            data=("not-json-" + raw_marker).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400
        assert await response.json() == {
            "error": {"code": "PUBSUB_ENVELOPE_INVALID"}
        }

    canonical = replace(config, mode=FaultPipelineMode.CANONICAL)
    async with TestClient(
        TestServer(
            create_app(canonical, Repository(failure=True), clock=lambda: fixed_now)
        )
    ) as client:
        response = await client.post(
            "/events/pubsub",
            data=push_body(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 503
        assert response.headers["Retry-After"] == "5"
        assert "raw-secret" not in await response.text()
    assert raw_marker not in caplog.text
    assert "raw-secret-backend-error" not in caplog.text


@pytest.mark.asyncio
async def test_surrogate_poison_is_fixed_400_without_log_reflection(
    config, fixed_now, caplog
) -> None:
    from .conftest import log_entry

    payload = log_entry()
    payload["logName"] = "private-marker-\ud800"
    async with TestClient(
        TestServer(create_app(config, Repository(), clock=lambda: fixed_now))
    ) as client:
        response = await client.post(
            "/events/pubsub",
            data=push_body(payload),
            headers={"Content-Type": "application/json"},
        )
        response_body = await response.json()
    assert response.status == 400
    assert response_body == {
        "error": {"code": "PUBSUB_DATA_JSON_INVALID"}
    }
    assert "private-marker" not in caplog.text
