from __future__ import annotations

import json
from datetime import timedelta

import pytest

from telco_fault_ingress.boundary import parse_pubsub_push
from telco_fault_ingress.models import PermanentIngressError
from telco_fault_ingress.normalizer import (
    build_incident_trigger,
    normalize_fault_event,
)

from .conftest import log_entry, push_body


def test_redelivery_produces_stable_independent_canonical_ids(config, fixed_now) -> None:
    first = normalize_fault_event(
        parse_pubsub_push(push_body(message_id="delivery-1"), config),
        received_at=fixed_now,
    )
    second = normalize_fault_event(
        parse_pubsub_push(push_body(message_id="delivery-2"), config),
        received_at=fixed_now,
    )
    assert first == second
    trigger = build_incident_trigger(first)
    identifiers = {
        first.source_event_id,
        first.trace_id,
        trigger.message_id,
        trigger.workflow_id,
        trigger.incident_id,
        trigger.idempotency_key,
    }
    assert len(identifiers) == 6
    assert trigger.incident.source_event_ids == (first.source_event_id,)


def test_userid_is_not_read_retained_or_hashed_into_correlation(config, fixed_now) -> None:
    first_payload = log_entry(event_type="CRITICALSERVICEERROR")
    second_payload = log_entry(event_type="CRITICALSERVICEERROR")
    second_payload["jsonPayload"] = {
        "node": "upf-node-1",
        "userid": "imsi-999999999999999",
    }
    first = normalize_fault_event(
        parse_pubsub_push(push_body(first_payload), config), received_at=fixed_now
    )
    second = normalize_fault_event(
        parse_pubsub_push(push_body(second_payload), config), received_at=fixed_now
    )
    assert first.incident.correlation_key == second.incident.correlation_key
    serialized = json.dumps(
        build_incident_trigger(first).to_data_part(), sort_keys=True
    ).lower()
    assert "userid" not in serialized
    assert "imsi" not in serialized


def test_unknown_event_and_unsafe_resource_are_poison(config, fixed_now) -> None:
    unknown = log_entry(event_type="NOT_APPROVED")
    with pytest.raises(PermanentIngressError) as captured:
        normalize_fault_event(
            parse_pubsub_push(push_body(unknown), config), received_at=fixed_now
        )
    assert captured.value.code == "FAULT_EVENT_TYPE_REJECTED"

    unsafe = log_entry()
    unsafe["jsonPayload"] = {"process_name": "nr ue", "hostname": "node"}
    with pytest.raises(PermanentIngressError) as captured:
        normalize_fault_event(
            parse_pubsub_push(push_body(unsafe), config), received_at=fixed_now
        )
    assert captured.value.code == "FAULT_RESOURCE_INVALID"


def test_naive_or_missing_event_timestamp_is_poison(config, fixed_now) -> None:
    payload = log_entry()
    payload["timestamp"] = "2026-08-28T01:02:03"
    with pytest.raises(PermanentIngressError) as captured:
        normalize_fault_event(
            parse_pubsub_push(push_body(payload), config), received_at=fixed_now
        )
    assert captured.value.code == "FAULT_TIMESTAMP_INVALID"


@pytest.mark.parametrize(
    ("delta", "code"),
    [
        (timedelta(seconds=5 * 60 + 1), "FAULT_TIMESTAMP_FUTURE"),
        (timedelta(seconds=-(7 * 24 * 60 * 60 + 1)), "FAULT_TIMESTAMP_STALE"),
    ],
)
def test_event_timestamp_must_be_inside_trusted_time_budget(
    config, fixed_now, delta: timedelta, code: str
) -> None:
    payload = log_entry()
    payload["timestamp"] = (fixed_now + delta).isoformat().replace("+00:00", "Z")
    with pytest.raises(PermanentIngressError) as captured:
        normalize_fault_event(
            parse_pubsub_push(push_body(payload), config),
            received_at=fixed_now,
            max_event_age_seconds=config.max_event_age_seconds,
            max_future_skew_seconds=config.max_future_skew_seconds,
        )
    assert captured.value.code == code
