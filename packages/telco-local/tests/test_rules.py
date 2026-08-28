from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import telco_local.rules as rules_module

from telco_domain import (
    Incident,
    KpiComparator,
    KpiViolation,
    RuleRepository,
    Technology,
)
from telco_local.rules import (
    JsonRuleRepository,
    RcaRule,
    RuleLoadError,
    rule_content_sha256,
)


BASE_TIME = datetime(2025, 11, 24, 18, 18, 40, tzinfo=UTC)


def _rule_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "rule_id": "lte.erab.security-setup",
        "version": "1.0.0",
        "technology": "LTE",
        "is_current": True,
        "description_zh": "ERAB 成功率异常时检查 S1 安全配置失败占比。",
        "detection": {
            "kpi_name": "erab_success_rate",
            "comparator": "LT",
            "threshold": 97.0,
            "unit": "%",
            "max_gap_minutes": 15,
        },
        "analysis": {
            "evidence_types": ["TRACE"],
            "when": {
                "operator": "ALL",
                "predicates": [
                    {
                        "fact": "failed_security_setup_ratio",
                        "comparator": "GTE",
                        "value": 0.5,
                    },
                    {
                        "fact": "failure_count",
                        "comparator": "GTE",
                        "value": 1,
                    },
                ],
            },
            "hypothesis_zh": "S1 安全配置失败可能导致 ERAB 建立异常。",
            "root_cause_zh": "失败事件主要由 S1 安全配置失败造成。",
        },
        "severity": {
            "cases": [
                {
                    "when": {
                        "operator": "ALL",
                        "predicates": [
                            {
                                "fact": "kpi_value",
                                "comparator": "LT",
                                "value": 95.0,
                            }
                        ],
                    },
                    "severity": "HIGH",
                }
            ],
            "default": "LOW",
        },
    }
    payload.update(updates)
    return payload


def _incident(value: float = 94.5) -> Incident:
    return Incident(
        incident_id="incident-erab",
        trace_id="trace-erab",
        technology=Technology.LTE,
        detected_at=BASE_TIME,
        window_start=datetime(2025, 11, 24, 15, 0, tzinfo=UTC),
        window_end=BASE_TIME,
        violated_kpis=(
            KpiViolation(
                kpi_name="erab_success_rate",
                observed_value=value,
                threshold_value=97.0,
                comparator=KpiComparator.LT,
                unit="%",
                rule_id="lte.erab.security-setup",
                rule_version="1.0.0",
            ),
        ),
        rule_versions={"lte.erab.security-setup": "1.0.0"},
    )


def test_rule_model_is_strict_and_structured() -> None:
    rule = RcaRule.model_validate(_rule_payload())

    assert rule.schema_version == "1.0"
    assert rule.technology == "LTE"
    assert rule.detection.max_gap_minutes == 15
    assert rule.analysis.when.predicates[0].fact == "failed_security_setup_ratio"
    assert rule.severity.cases[0].severity.value == "HIGH"

    malformed = _rule_payload()
    malformed["unexpected"] = "not allowed"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RcaRule.model_validate(malformed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("version", "latest"),
        ("technology", "5G_SA"),
        ("is_current", "yes"),
    ],
)
def test_rule_model_rejects_invalid_versioning_and_scope(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        RcaRule.model_validate(_rule_payload(**{field: value}))


def test_rule_model_requires_detection_gap_and_rejects_unknown_nested_fields() -> None:
    payload = _rule_payload()
    detection = dict(payload["detection"])
    detection.pop("max_gap_minutes")
    payload["detection"] = detection
    with pytest.raises(ValidationError):
        RcaRule.model_validate(payload)

    payload = _rule_payload()
    analysis = dict(payload["analysis"])
    analysis["prompt"] = "free-form instructions are not executable rules"
    payload["analysis"] = analysis
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RcaRule.model_validate(payload)


def test_repository_loads_current_rules_in_stable_order_and_matches_protocol(
    tmp_path,
) -> None:
    active = _rule_payload()
    inactive = _rule_payload(
        rule_id="lte.erab.legacy",
        version="0.9.0",
        is_current=False,
    )
    later = _rule_payload(rule_id="lte.zzz", version="2.0.0")
    (tmp_path / "z.json").write_text(json.dumps(later), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(active), encoding="utf-8")
    (tmp_path / "legacy.json").write_text(json.dumps(inactive), encoding="utf-8")

    repository = JsonRuleRepository(tmp_path)

    assert isinstance(repository, RuleRepository)
    assert [rule.rule_id for rule in repository.load_all()] == [
        "lte.erab.security-setup",
        "lte.zzz",
    ]


def test_repository_match_typed_and_mapping_share_the_same_rule(tmp_path) -> None:
    (tmp_path / "rule.json").write_text(
        json.dumps(_rule_payload()), encoding="utf-8"
    )
    repository = JsonRuleRepository(tmp_path)

    typed = __import__("asyncio").run(repository.match_typed(_incident()))
    mapped = __import__("asyncio").run(repository.match(_incident()))

    assert typed == (RcaRule.model_validate(_rule_payload()),)
    assert mapped == (typed[0].model_dump(mode="json"),)
    assert __import__("asyncio").run(repository.match(_incident(98.0))) == ()


def test_repository_rejects_duplicate_rule_identity(tmp_path) -> None:
    payload = _rule_payload()
    (tmp_path / "one.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleLoadError, match="duplicate rule"):
        JsonRuleRepository(tmp_path).load_all()


def test_repository_rejects_two_current_versions_of_one_rule(tmp_path) -> None:
    (tmp_path / "one.json").write_text(
        json.dumps(_rule_payload(version="1.0.0")), encoding="utf-8"
    )
    (tmp_path / "two.json").write_text(
        json.dumps(_rule_payload(version="1.1.0")), encoding="utf-8"
    )

    with pytest.raises(RuleLoadError, match="multiple current versions"):
        JsonRuleRepository(tmp_path).load_all()


def test_repository_masks_invalid_file_content(tmp_path) -> None:
    raw_secret = "imsi=208930000000001"
    (tmp_path / "bad.json").write_text(raw_secret, encoding="utf-8")

    with pytest.raises(RuleLoadError) as error:
        JsonRuleRepository(tmp_path).load_all()

    assert raw_secret not in str(error.value)


@pytest.mark.parametrize(
    "field",
    ("description_zh", "hypothesis_zh", "root_cause_zh"),
)
def test_repository_rejects_sensitive_text_in_valid_rule_fields(
    tmp_path,
    field: str,
) -> None:
    raw_identifier = "208930000000001"
    payload = _rule_payload()
    if field == "description_zh":
        payload[field] = f"IMSI={raw_identifier}"
    else:
        analysis = dict(payload["analysis"])
        analysis[field] = f"IMSI={raw_identifier}"
        payload["analysis"] = analysis
    (tmp_path / "unsafe.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(RuleLoadError) as error:
        JsonRuleRepository(tmp_path).load_all_versions()

    assert raw_identifier not in str(error.value)
    assert "imsi" not in str(error.value).lower()


def test_repository_rejects_duplicate_json_object_keys(tmp_path) -> None:
    encoded = json.dumps(_rule_payload(), ensure_ascii=False)
    encoded = encoded.replace(
        '"threshold": 97.0',
        '"threshold": 1.0, "threshold": 97.0',
    )
    (tmp_path / "duplicate-key.json").write_text(encoded, encoding="utf-8")

    with pytest.raises(RuleLoadError, match="invalid"):
        JsonRuleRepository(tmp_path).load_all_versions()


def test_repository_bounds_rule_file_count(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rules_module, "MAX_RULE_FILES", 1)
    (tmp_path / "one.json").write_text(
        json.dumps(_rule_payload(rule_id="lte.one")), encoding="utf-8"
    )
    (tmp_path / "two.json").write_text(
        json.dumps(_rule_payload(rule_id="lte.two")), encoding="utf-8"
    )

    with pytest.raises(RuleLoadError, match="file count"):
        JsonRuleRepository(tmp_path).load_all_versions()


def test_repository_bounds_total_rule_bytes(tmp_path, monkeypatch) -> None:
    one = json.dumps(_rule_payload(rule_id="lte.one"))
    two = json.dumps(_rule_payload(rule_id="lte.two"))
    monkeypatch.setattr(
        rules_module,
        "MAX_RULE_TOTAL_BYTES",
        len(one.encode("utf-8")) + len(two.encode("utf-8")) - 1,
    )
    (tmp_path / "one.json").write_text(one, encoding="utf-8")
    (tmp_path / "two.json").write_text(two, encoding="utf-8")

    with pytest.raises(RuleLoadError, match="total byte"):
        JsonRuleRepository(tmp_path).load_all_versions()


def test_repository_resolves_exact_historical_version_for_rca(tmp_path) -> None:
    historical = _rule_payload(version="1.0.0", is_current=False)
    current = _rule_payload(
        version="2.0.0",
        is_current=True,
        detection={
            "kpi_name": "erab_success_rate",
            "comparator": "LT",
            "threshold": 90.0,
            "unit": "%",
            "max_gap_minutes": 15,
        },
    )
    (tmp_path / "historical.json").write_text(
        json.dumps(historical), encoding="utf-8"
    )
    (tmp_path / "current.json").write_text(
        json.dumps(current), encoding="utf-8"
    )
    repository = JsonRuleRepository(tmp_path)

    resolution = __import__("asyncio").run(
        repository.resolve_typed(_incident())
    )

    assert [rule.version for rule in repository.load_all()] == ["2.0.0"]
    assert resolution.status == "EXACT"
    assert [rule.version for rule in resolution.rules] == ["1.0.0"]
    assert __import__("asyncio").run(repository.match_typed(_incident())) == (
        RcaRule.model_validate(historical),
    )


@pytest.mark.parametrize(
    "changed_field",
    ("hypothesis", "root_cause", "severity"),
)
def test_repository_rejects_same_version_rule_content_drift(
    tmp_path,
    changed_field: str,
) -> None:
    original = RcaRule.model_validate(_rule_payload())
    changed = original.model_dump(mode="json", round_trip=True)
    if changed_field == "hypothesis":
        changed["analysis"]["hypothesis_zh"] = "同版本下被替换的假设。"
    elif changed_field == "root_cause":
        changed["analysis"]["root_cause_zh"] = "同版本下被替换的根因。"
    else:
        changed["severity"]["default"] = "MEDIUM"
    (tmp_path / "changed.json").write_text(
        json.dumps(changed, ensure_ascii=False),
        encoding="utf-8",
    )
    incident_payload = _incident().model_dump(mode="python", round_trip=True)
    incident_payload["model_metadata"] = {
        "detector_algorithm": "deterministic-threshold-episodes-v3",
        "rule_content_hashes": {
            original.rule_id: rule_content_sha256(original),
        },
    }

    resolution = __import__("asyncio").run(
        JsonRuleRepository(tmp_path).resolve_typed(
            Incident.model_validate(incident_payload)
        )
    )

    assert resolution.status == "CONFLICT"
    assert resolution.rules == ()
    assert [issue.code for issue in resolution.issues] == [
        "RULE_CONTENT_MISMATCH"
    ]


def test_repository_never_falls_back_when_historical_version_is_missing(
    tmp_path,
) -> None:
    current = _rule_payload(version="2.0.0", is_current=True)
    (tmp_path / "current.json").write_text(
        json.dumps(current), encoding="utf-8"
    )
    repository = JsonRuleRepository(tmp_path)

    resolution = __import__("asyncio").run(
        repository.resolve_typed(_incident())
    )

    assert resolution.status == "CONFLICT"
    assert resolution.rules == ()
    assert __import__("asyncio").run(repository.match_typed(_incident())) == ()


@pytest.mark.parametrize(
    ("violation_update", "issue_code"),
    [
        ({"unit": "ratio"}, "UNIT_MISMATCH"),
        ({"threshold_value": 96.0}, "THRESHOLD_MISMATCH"),
        ({"comparator": "GT"}, "COMPARATOR_MISMATCH"),
    ],
)
def test_repository_rejects_rule_binding_conflicts(
    tmp_path,
    violation_update: dict[str, object],
    issue_code: str,
) -> None:
    (tmp_path / "rule.json").write_text(
        json.dumps(_rule_payload()), encoding="utf-8"
    )
    incident = _incident()
    violation_payload = incident.violated_kpis[0].model_dump(
        mode="python", round_trip=True
    )
    violation_payload.update(violation_update)
    incident_payload = incident.model_dump(mode="python", round_trip=True)
    incident_payload["violated_kpis"] = (KpiViolation.model_validate(violation_payload),)

    resolution = __import__("asyncio").run(
        JsonRuleRepository(tmp_path).resolve_typed(
            Incident.model_validate(incident_payload)
        )
    )

    assert resolution.status == "CONFLICT"
    assert resolution.rules == ()
    assert [issue.code for issue in resolution.issues] == [issue_code]


def test_repository_marks_unversioned_legacy_without_guessing_current(
    tmp_path,
) -> None:
    (tmp_path / "rule.json").write_text(
        json.dumps(_rule_payload()), encoding="utf-8"
    )
    incident = _incident()
    violation_payload = incident.violated_kpis[0].model_dump(
        mode="python", round_trip=True
    )
    violation_payload.update(rule_id=None, rule_version=None)
    incident_payload = incident.model_dump(mode="python", round_trip=True)
    incident_payload.update(
        violated_kpis=(KpiViolation.model_validate(violation_payload),),
        rule_versions={},
    )

    resolution = __import__("asyncio").run(
        JsonRuleRepository(tmp_path).resolve_typed(
            Incident.model_validate(incident_payload)
        )
    )

    assert resolution.status == "LEGACY_UNVERSIONED"
    assert resolution.rules == ()
    assert [issue.code for issue in resolution.issues] == [
        "LEGACY_UNVERSIONED"
    ]


def test_repository_rejects_incident_and_violation_version_conflict(
    tmp_path,
) -> None:
    (tmp_path / "historical.json").write_text(
        json.dumps(_rule_payload(version="1.0.0", is_current=False)),
        encoding="utf-8",
    )
    (tmp_path / "current.json").write_text(
        json.dumps(_rule_payload(version="2.0.0", is_current=True)),
        encoding="utf-8",
    )
    incident_payload = _incident().model_dump(mode="python", round_trip=True)
    incident_payload["rule_versions"] = {
        "lte.erab.security-setup": "2.0.0"
    }

    resolution = __import__("asyncio").run(
        JsonRuleRepository(tmp_path).resolve_typed(
            Incident.model_validate(incident_payload)
        )
    )

    assert resolution.status == "CONFLICT"
    assert resolution.rules == ()
    assert [issue.code for issue in resolution.issues] == [
        "INCIDENT_VERSION_CONFLICT"
    ]
