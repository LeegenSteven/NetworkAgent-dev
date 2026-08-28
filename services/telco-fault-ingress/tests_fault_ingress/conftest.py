from __future__ import annotations

import base64
from datetime import UTC, datetime
import json

import pytest

from telco_fault_ingress.config import FaultIngressConfig


SUBSCRIPTION = "projects/demo/subscriptions/network-fault-canonical"


def log_entry(
    *,
    event_type: str = "UERANSIMHEALTH",
    insert_id: str = "insert-1",
) -> dict[str, object]:
    json_payload: dict[str, object]
    if event_type == "UERANSIMHEALTH":
        json_payload = {"process_name": "nr-ue", "hostname": "ran-node-1"}
    else:
        json_payload = {
            "node": "upf-node-1",
            "userid": "imsi-001010000000001",
        }
    return {
        "insertId": insert_id,
        "logName": "projects/demo/logs/faults",
        "timestamp": "2026-08-28T01:02:03Z",
        "labels": {"python_logger": event_type},
        "jsonPayload": json_payload,
    }


def push_body(
    payload: dict[str, object] | None = None,
    *,
    subscription: str = SUBSCRIPTION,
    message_id: str = "delivery-1",
) -> bytes:
    encoded = base64.b64encode(
        json.dumps(payload or log_entry(), separators=(",", ":")).encode()
    ).decode()
    return json.dumps(
        {
            "message": {
                "data": encoded,
                "messageId": message_id,
                "publishTime": "2026-08-28T01:02:04Z",
                "attributes": {"source": "logging"},
            },
            "subscription": subscription,
            "deliveryAttempt": 1,
        },
        separators=(",", ":"),
    ).encode()


@pytest.fixture
def config() -> FaultIngressConfig:
    return FaultIngressConfig(allowed_subscriptions=frozenset({SUBSCRIPTION}))


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 8, 28, 1, 2, 5, tzinfo=UTC)
