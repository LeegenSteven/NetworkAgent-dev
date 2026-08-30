from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from telco_lab.replay import (
    ReplayError,
    ReplayWirePayload,
    replay_wire_payload_from_event,
    validate_replay_wire_payload,
)
from telco_lab.schema import canonical_json_bytes

from test_replay import _plan, _source


def _legacy_sink_payload(event) -> dict[str, object]:  # noqa: ANN001
    return {
        "schema_version": event.schema_version,
        "source_event_id": event.source_event_id,
        "idempotency_key": event.idempotency_key,
        "payload_sha256": event.payload_sha256,
        "lock_id": event.lock_id,
        "bundle_id": event.bundle_id,
        "source_observation_id": event.source_observation_id,
        "dataset_id": event.dataset_id,
        "dataset_version": event.dataset_version,
        "scenario": event.scenario,
        "replay_observed_at": event.replay_observed_at,
        "resource_id": event.resource_id,
        "technology": event.technology,
        "metrics": {key: event.metrics[key] for key in sorted(event.metrics)},
        "units": {key: event.units[key] for key in sorted(event.units)},
        "quality_flags": event.quality_flags,
    }


def test_wire_from_event_preserves_legacy_sink_bytes_and_has_body_fingerprint(
    tmp_path,
) -> None:  # noqa: ANN001
    event = _plan(_source(tmp_path / "workspace")).events[0]
    expected = _legacy_sink_payload(event)

    wire = replay_wire_payload_from_event(event)

    assert isinstance(wire, ReplayWirePayload)
    assert canonical_json_bytes(wire.to_sink_payload()) == canonical_json_bytes(
        expected
    )
    assert canonical_json_bytes(event.sink_payload()) == canonical_json_bytes(expected)
    assert (
        wire.request_fingerprint_sha256
        == hashlib.sha256(canonical_json_bytes(expected)).hexdigest()
    )
    assert "request_fingerprint_sha256" not in wire.to_sink_payload()
    assert "request_fingerprint_sha256" not in ReplayWirePayload.model_fields
    with pytest.raises(ValidationError):
        wire.scenario = "changed"  # type: ignore[misc]


def test_wire_validation_accepts_only_strict_json_decoded_object(tmp_path) -> None:
    event = _plan(_source(tmp_path / "workspace")).events[0]
    expected = _legacy_sink_payload(event)
    decoded = json.loads(canonical_json_bytes(expected))

    wire = validate_replay_wire_payload(decoded)

    assert wire == replay_wire_payload_from_event(event)
    assert wire.replay_observed_at == event.replay_observed_at
    assert canonical_json_bytes(wire.to_sink_payload()) == canonical_json_bytes(
        expected
    )
    for invalid in (
        canonical_json_bytes(expected),
        canonical_json_bytes(expected).decode("utf-8"),
        [decoded],
        None,
    ):
        with pytest.raises(ReplayError) as caught:
            validate_replay_wire_payload(invalid)
        assert caught.value.code == "replay_wire_invalid"


def _wire_attacks(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    attacks: list[dict[str, object]] = []

    def changed(**values: object) -> dict[str, object]:
        candidate = copy.deepcopy(payload)
        candidate.update(values)
        return candidate

    attacks.extend(
        (
            changed(schema_version="2.0"),
            changed(payload_sha256="0" * 64),
            changed(source_event_id="labevent-" + "0" * 64),
            changed(idempotency_key="labidempotency-" + "0" * 64),
            changed(lock_id="lablock-" + "0" * 64),
            changed(source_observation_id="labobs-" + "0" * 64),
            changed(replay_observed_at="2026-08-30T17:00:00+08:00"),
            changed(scenario="x" * 129),
            changed(ground_truth=True),
        )
    )
    bad_metric = copy.deepcopy(payload)
    bad_metric["metrics"] = {"persistent_interference": 1.0}
    bad_metric["units"] = {"persistent_interference": "count"}
    attacks.append(bad_metric)
    bad_unit = copy.deepcopy(payload)
    first_metric = next(iter(bad_unit["units"]))  # type: ignore[arg-type]
    bad_unit["units"][first_metric] = "subscriber-secret"  # type: ignore[index]
    attacks.append(bad_unit)
    bad_flag = copy.deepcopy(payload)
    bad_flag["quality_flags"] = ["persistent_interference"]
    attacks.append(bad_flag)
    non_json_flag = copy.deepcopy(payload)
    non_json_flag["quality_flags"] = tuple(non_json_flag["quality_flags"])  # type: ignore[arg-type]
    attacks.append(non_json_flag)
    boolean_metric = copy.deepcopy(payload)
    first_value = next(iter(boolean_metric["metrics"]))  # type: ignore[arg-type]
    boolean_metric["metrics"][first_value] = True  # type: ignore[index]
    attacks.append(boolean_metric)
    overflowing_metric = copy.deepcopy(payload)
    overflowing_metric["metrics"][first_value] = 10**10_000  # type: ignore[index]
    attacks.append(overflowing_metric)
    return tuple(attacks)


def test_wire_recomputes_all_identity_and_projection_guards(tmp_path) -> None:
    event = _plan(_source(tmp_path / "workspace")).events[0]
    decoded = json.loads(canonical_json_bytes(_legacy_sink_payload(event)))

    for attack_index, attack in enumerate(_wire_attacks(decoded)):
        error: ReplayError | None = None
        try:
            validate_replay_wire_payload(attack)
        except ReplayError as caught:
            error = caught
        if error is None:
            pytest.fail(f"wire attack {attack_index} was accepted")
        assert error.code == "replay_wire_invalid"
        assert "persistent_interference" not in str(error)
        assert "subscriber-secret" not in str(error)


def test_event_sink_payload_delegates_to_public_wire_contract(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _plan(_source(tmp_path / "workspace")).events[0]
    original = ReplayWirePayload.from_event.__func__
    calls = 0

    def tracked(cls, value):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return original(cls, value)

    monkeypatch.setattr(ReplayWirePayload, "from_event", classmethod(tracked))

    assert event.sink_payload() == _legacy_sink_payload(event)
    assert calls == 1


def test_wire_public_exports() -> None:
    from telco_lab import (
        ReplayWirePayload as ExportedWire,
        replay_wire_payload_from_event as exported_from_event,
        validate_replay_wire_payload as exported_validate,
    )

    assert ExportedWire is ReplayWirePayload
    assert exported_from_event is replay_wire_payload_from_event
    assert exported_validate is validate_replay_wire_payload
