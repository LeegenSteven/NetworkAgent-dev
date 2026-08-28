from __future__ import annotations

import base64
import json

import pytest

from telco_fault_ingress.boundary import parse_pubsub_push
from telco_fault_ingress.models import PermanentIngressError

from .conftest import SUBSCRIPTION, push_body


def test_valid_push_is_decoded_without_retaining_body(config) -> None:
    parsed = parse_pubsub_push(push_body(), config)
    assert parsed.subscription == SUBSCRIPTION
    assert parsed.message_id == "delivery-1"
    assert parsed.payload["insertId"] == "insert-1"
    assert len(parsed.payload_sha256) == 64
    assert parsed.attributes == {}
    assert not hasattr(parsed, "body")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda body: {**body, "unexpected": True}, "PUBSUB_ENVELOPE_INVALID"),
        (
            lambda body: {**body, "subscription": "projects/evil/subscriptions/x"},
            "PUBSUB_SUBSCRIPTION_REJECTED",
        ),
        (
            lambda body: {
                **body,
                "message": {**body["message"], "data": "not-base64***"},
            },
            "PUBSUB_DATA_INVALID",
        ),
    ],
)
def test_envelope_errors_use_fixed_codes(config, mutation, code) -> None:
    original = json.loads(push_body())
    body = json.dumps(mutation(original)).encode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(body, config)
    assert captured.value.code == code


def test_decoded_payload_must_be_json_object(config) -> None:
    outer = json.loads(push_body())
    outer["message"]["data"] = base64.b64encode(b"[]").decode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


def test_nested_payload_is_rejected_before_normalization(config) -> None:
    nested: object = {"leaf": True}
    for _ in range(config.max_json_depth + 1):
        nested = {"nested": nested}
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(push_body(nested), config)  # type: ignore[arg-type]
    assert captured.value.code == "PUBSUB_DATA_TOO_DEEP"


def test_request_and_decoded_size_budgets_are_independent(config) -> None:
    tiny = type(config)(
        allowed_subscriptions=config.allowed_subscriptions,
        max_request_bytes=10_000,
        max_decoded_bytes=100,
    )
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(push_body({"value": "x" * 200}), tiny)
    assert captured.value.code == "PUBSUB_DATA_SIZE_INVALID"


def test_duplicate_keys_are_rejected_in_both_json_layers(config) -> None:
    outer = json.loads(push_body())
    message = json.dumps(outer["message"], separators=(",", ":"))
    subscription = json.dumps(outer["subscription"])
    duplicate_outer = (
        f'{{"message":{message},"subscription":{subscription},'
        f'"subscription":{subscription}}}'
    ).encode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(duplicate_outer, config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"

    outer["message"]["data"] = base64.b64encode(
        b'{"value":1,"value":2}'
    ).decode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_rejected_in_both_json_layers(
    config, constant: str
) -> None:
    outer_text = push_body().decode().replace(
        '"deliveryAttempt":1', f'"deliveryAttempt":{constant}'
    )
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(outer_text.encode(), config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"

    outer = json.loads(push_body())
    outer["message"]["data"] = base64.b64encode(
        f'{{"value":{constant}}}'.encode()
    ).decode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


def test_arbitrary_pubsub_attributes_are_discarded(config) -> None:
    outer = json.loads(push_body())
    outer["message"]["attributes"] = {
        "authorization": "subscriber-private-marker",
        "untrusted": "value",
    }
    parsed = parse_pubsub_push(json.dumps(outer).encode(), config)
    assert parsed.attributes == {}
    assert "subscriber-private-marker" not in repr(parsed)


def test_nonstandard_snake_case_pubsub_wire_fields_are_rejected(config) -> None:
    outer = json.loads(push_body())
    outer["message"]["message_id"] = outer["message"].pop("messageId")
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"


def test_both_json_layers_require_strict_utf8(config) -> None:
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(push_body().decode().encode("utf-16"), config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"

    outer = json.loads(push_body())
    decoded = json.dumps(
        {"insertId": "insert-utf16"}, separators=(",", ":")
    ).encode("utf-16")
    outer["message"]["data"] = base64.b64encode(decoded).decode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


def test_overflowing_json_numbers_are_rejected_in_both_layers(config) -> None:
    outer_text = push_body().decode().replace(
        '"deliveryAttempt":1', '"deliveryAttempt":1e9999'
    )
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(outer_text.encode(), config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"

    outer = json.loads(push_body())
    outer["message"]["data"] = base64.b64encode(b'{"value":1e9999}').decode()
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


@pytest.mark.parametrize(
    "payload",
    [
        {"logName": "\ud800"},
        {"insertId": "\udfff"},
        {"jsonPayload": {"unsafe": "\ud800"}},
        {"jsonPayload": {"\udfff": "value"}},
    ],
)
def test_unpaired_surrogates_in_keys_or_values_are_poison(config, payload) -> None:
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(push_body(payload), config)
    assert captured.value.code == "PUBSUB_DATA_JSON_INVALID"


def test_outer_json_surrogate_key_is_poison(config) -> None:
    outer = json.loads(push_body())
    outer["\ud800"] = "value"
    with pytest.raises(PermanentIngressError) as captured:
        parse_pubsub_push(json.dumps(outer).encode(), config)
    assert captured.value.code == "PUBSUB_ENVELOPE_INVALID"
