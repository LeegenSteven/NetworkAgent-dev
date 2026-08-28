"""Strict Pub/Sub push decoding with bounded, non-reflective errors."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import math
from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from .config import FaultIngressConfig
from .models import ParsedPubSubPush, PermanentIngressError


BoundedString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]


class _PubSubMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    data: str = Field(min_length=1)
    message_id: BoundedString = Field(alias="messageId")
    publish_time: AwareDatetime = Field(alias="publishTime")
    attributes: dict[str, str] = Field(default_factory=dict)
    ordering_key: str = Field(default="", alias="orderingKey", max_length=1024)

    @field_validator("attributes")
    @classmethod
    def _bounded_attributes(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("too many Pub/Sub attributes")
        if any(len(key) > 256 or len(item) > 1024 for key, item in value.items()):
            raise ValueError("Pub/Sub attribute is too long")
        # No Pub/Sub attribute participates in the canonical fault contract.
        # Drop the entire untrusted map at the transport boundary so arbitrary
        # or privacy-sensitive metadata cannot cross into normalization.
        return {}


class _PubSubPush(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: _PubSubMessage
    subscription: BoundedString
    delivery_attempt: int | None = Field(
        default=None, alias="deliveryAttempt", ge=1, le=10_000
    )


def _payload_depth(value: object, maximum: int) -> int:
    deepest = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        deepest = max(deepest, depth)
        if depth > maximum:
            return depth
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend((item, depth + 1) for item in current)
    return deepest


def _json_object(data: bytes, *, code: str) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def finite_constant(_: str) -> None:
        raise ValueError("non-finite JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    try:
        text = data.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=finite_constant,
            parse_float=finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise PermanentIngressError(code) from None
    if not isinstance(parsed, Mapping):
        raise PermanentIngressError(code)
    # Defense in depth: parse_float/parse_constant cover the standard decoder
    # paths, while this walk makes the finite-number invariant explicit at the
    # boundary before either JSON layer is returned.
    stack: list[object] = [parsed]
    while stack:
        current = stack.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise PermanentIngressError(code)
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise PermanentIngressError(code) from None
        if isinstance(current, Mapping):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            stack.extend(current)
    return parsed


def parse_pubsub_push(
    body: bytes, config: FaultIngressConfig
) -> ParsedPubSubPush:
    """Parse one request without retaining or reflecting its raw bytes."""

    if not body or len(body) > config.max_request_bytes:
        raise PermanentIngressError("PUBSUB_REQUEST_SIZE_INVALID")
    outer = _json_object(body, code="PUBSUB_ENVELOPE_INVALID")
    if _payload_depth(outer, config.max_json_depth) > config.max_json_depth:
        raise PermanentIngressError("PUBSUB_ENVELOPE_TOO_DEEP")
    try:
        push = _PubSubPush.model_validate(outer)
    except ValidationError:
        raise PermanentIngressError("PUBSUB_ENVELOPE_INVALID") from None
    if push.subscription not in config.allowed_subscriptions:
        raise PermanentIngressError("PUBSUB_SUBSCRIPTION_REJECTED")

    try:
        encoded = push.message.data.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise PermanentIngressError("PUBSUB_DATA_INVALID") from None
    if not decoded or len(decoded) > config.max_decoded_bytes:
        raise PermanentIngressError("PUBSUB_DATA_SIZE_INVALID")
    payload = _json_object(decoded, code="PUBSUB_DATA_JSON_INVALID")
    if _payload_depth(payload, config.max_json_depth) > config.max_json_depth:
        raise PermanentIngressError("PUBSUB_DATA_TOO_DEEP")

    return ParsedPubSubPush(
        subscription=push.subscription,
        message_id=push.message.message_id,
        publish_time=push.message.publish_time.astimezone(UTC),
        delivery_attempt=push.delivery_attempt,
        attributes=dict(push.message.attributes),
        payload=dict(payload),
        payload_sha256=hashlib.sha256(decoded).hexdigest(),
    )


__all__ = ["parse_pubsub_push"]
