from __future__ import annotations

import pytest

from telco_domain.privacy import (
    REDACTED,
    SensitiveDataError,
    assert_model_safe,
    find_sensitive_paths,
    redact_sensitive_data,
)


def test_nested_subscriber_identifiers_are_detected_without_values() -> None:
    payload = {
        "incident": {
            "imsi": "208930000000001",
            "evidence": ["MSISDN: 0900000001", {"result": "aggregated"}],
        }
    }

    paths = find_sensitive_paths(payload)

    assert paths == ("$.incident.imsi", "$.incident.evidence[0]")


def test_assertion_error_contains_paths_but_not_identifier_values() -> None:
    raw_identifier = "208930000000001"

    with pytest.raises(SensitiveDataError) as error:
        assert_model_safe({"subscriber": {"imeisv": raw_identifier}})

    assert "$.subscriber.imeisv" in str(error.value)
    assert raw_identifier not in str(error.value)


def test_labeled_identifier_in_mapping_key_is_rejected_without_echo() -> None:
    raw_key = "IMSI=208930000000001"

    with pytest.raises(SensitiveDataError) as error:
        assert_model_safe({"outcome_counts": {raw_key: 1}})

    assert raw_key not in str(error.value)
    assert "<sensitive-key>" in str(error.value)
    assert redact_sensitive_data({raw_key: 1}) == {REDACTED: 1}


def test_subscriber_identifier_inside_a_set_is_rejected() -> None:
    with pytest.raises(SensitiveDataError):
        assert_model_safe({"values": {"IMSI: 208930000000001"}})


def test_redaction_is_recursive_and_does_not_mutate_input() -> None:
    payload = {
        "imsi": "208930000000001",
        "notes": ["supi-imsi-208930000000002", "aggregated KPI only"],
    }

    redacted = redact_sensitive_data(payload)

    assert redacted == {
        "imsi": REDACTED,
        "notes": [f"supi-{REDACTED}", "aggregated KPI only"],
    }
    assert payload["imsi"] == "208930000000001"


def test_5g_concealed_and_equipment_identifiers_are_redacted() -> None:
    payload = {
        "suci": "suci-0-208-93-0-0-0-0000000001",
        "note": "IMEI=123456789012345",
    }

    redacted = redact_sensitive_data(payload)

    assert redacted["suci"] == REDACTED
    assert redacted["note"] == f"IMEI={REDACTED}"
    assert find_sensitive_paths(payload) == ("$.suci", "$.note")


def test_aggregate_fields_and_telecom_terms_are_safe() -> None:
    payload = {
        "imsi_count": 42,
        "description": "IMSI and MSISDN are excluded from this aggregate.",
        "connection_outcomes": {"SUCCESS": 144, "FAILED": 21},
    }

    assert find_sensitive_paths(payload) == ()
    assert_model_safe(payload)


@pytest.mark.parametrize("empty_value", [None, "", [], {}])
def test_empty_sensitive_fields_do_not_fail(empty_value: object) -> None:
    assert find_sensitive_paths({"msisdn": empty_value}) == ()
