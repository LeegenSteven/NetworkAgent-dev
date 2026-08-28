"""Deterministic Local Detector tests with no model or cloud dependency."""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import UTC, datetime
from uuid import UUID

import duckdb
import pytest

from telco_domain import (
    IdempotencyConflictError,
    IncidentStatus,
    SensitiveDataError,
    Technology,
)
from telco_local.database import initialize_database
from telco_local.detector import (
    DetectorCapacityError,
    LocalDetector,
    MAX_CURRENT_RULES,
    MAX_EPISODE_SAMPLES,
    MAX_SCAN_CANDIDATES,
)
from telco_local.incident_repository import DuckDbIncidentRepository
from telco_local.telemetry import DuckDbTelemetryRepository
from telco_local.telemetry import MAX_QUERY_OBSERVATIONS


FIXED_NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def _run(coroutine):
    return asyncio.run(coroutine)


def _rule(*, max_gap_minutes: int = 30) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "rule_id": "lte.erab.security-setup",
        "version": "1.0.0",
        "technology": "LTE",
        "is_current": True,
        "description_zh": "检测 ERAB 建立成功率异常。",
        "detection": {
            "kpi_name": "erab_success_rate",
            "comparator": "LT",
            "threshold": 97.0,
            "unit": "%",
            "max_gap_minutes": max_gap_minutes,
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
                    }
                ],
            },
            "hypothesis_zh": "S1 安全配置失败可能导致 ERAB 建立异常。",
            "root_cause_zh": "失败事件主要由 S1 安全配置失败造成。",
        },
        "severity": {"cases": [], "default": "LOW"},
    }


def _counter_row(
    template: dict[str, str],
    *,
    timestamp: str,
    attempts: int,
    successes: int,
) -> dict[str, object]:
    row: dict[str, object] = dict(template)
    row.update(
        EnodeB_id="7",
        cell_id="700",
        measurement_end=timestamp,
        ERAB_SessionTimeUE=1_000,
    )
    for qci in range(1, 10):
        row[f"ERAB_EstabInitAttNbr_QCI{qci}"] = attempts if qci == 1 else 0
        row[f"ERAB_EstabInitSuccNbr_QCI{qci}"] = successes if qci == 1 else 0
        row[f"ERAB_RelActNbr_QCI{qci}"] = 0
    return row


def _detector_config(local_config):
    with local_config.performance_csv_path.open(
        newline="", encoding="utf-8"
    ) as stream:
        reader = csv.DictReader(stream)
        template = next(reader)
        fieldnames = tuple(reader.fieldnames or ())

    # Violations at 00:00 and 00:30 belong to one episode because the rule's
    # inclusive maximum gap is 30 minutes.  01:15 starts a second episode.
    # The 00:15 sample is deliberately >100%; it must remain visible as a data
    # quality flag in aggregate evidence and must not be silently clipped.
    rows = (
        _counter_row(
            template,
            timestamp="11/20/2025 00:00:00",
            attempts=100,
            successes=90,
        ),
        _counter_row(
            template,
            timestamp="11/20/2025 00:15:00",
            attempts=100,
            successes=110,
        ),
        _counter_row(
            template,
            timestamp="11/20/2025 00:30:00",
            attempts=100,
            successes=90,
        ),
        _counter_row(
            template,
            timestamp="11/20/2025 01:15:00",
            attempts=100,
            successes=90,
        ),
        _counter_row(
            template,
            timestamp="11/20/2025 02:00:00",
            attempts=100,
            successes=97,
        ),
    )
    with local_config.performance_csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    for path in local_config.rules_dir.glob("*.json"):
        path.unlink()
    (local_config.rules_dir / "erab.json").write_text(
        json.dumps(_rule(), ensure_ascii=False),
        encoding="utf-8",
    )
    initialize_database(local_config, reset=True)
    return local_config


def _detector(config) -> LocalDetector:
    return LocalDetector(config, clock=lambda: FIXED_NOW)


def test_scan_is_read_only_deterministic_and_splits_on_rule_gap(local_config) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)

        first = await detector.scan("trace-detector")
        replay = await detector.scan("trace-detector")
        explicit_workflow = await detector.scan(
            "trace-detector",
            workflow_id="workflow-explicit",
        )

        assert tuple(item.incident for item in replay) == tuple(
            item.incident for item in first
        )
        assert len(first) == 2
        assert [item.incident.window_start.isoformat() for item in first] == [
            "2025-11-20T00:00:00+00:00",
            "2025-11-20T01:15:00+00:00",
        ]
        assert first[0].incident.window_end.isoformat() == (
            "2025-11-20T00:30:00+00:00"
        )
        assert len(first[0].incident.source_event_ids) == 2
        assert len(
            {
                first[0].message_id,
                first[0].workflow_id,
                first[0].idempotency_key,
                first[0].incident_id,
                first[0].trace_id,
            }
        ) == 5
        assert replay[0].message_id != first[0].message_id
        assert replay[0].idempotency_key != first[0].idempotency_key
        assert replay[0].workflow_id != first[0].workflow_id
        assert {item.workflow_id for item in first} == {
            first[0].workflow_id
        }
        assert {item.workflow_id for item in explicit_workflow} == {
            "workflow-explicit"
        }
        generated_envelope_ids = (
            first[0].workflow_id,
            *(item.message_id for item in first),
            *(item.idempotency_key for item in first),
        )
        assert len(set(generated_envelope_ids)) == len(
            generated_envelope_ids
        )
        assert all(UUID(value).version == 4 for value in generated_envelope_ids)
        assert first[0].incident.detected_at == first[0].incident.window_end
        assert first[0].incident.created_at == first[0].incident.window_end
        assert first[0].incident.updated_at == first[0].incident.window_end

        for trigger in first:
            incident = trigger.incident
            assert incident.status is IncidentStatus.DETECTED
            assert incident.technology is Technology.LTE
            assert all(
                resource.technology is Technology.LTE
                for resource in incident.affected_resources
            )
            assert incident.violated_kpis[0].threshold_value == 97
            assert incident.violated_kpis[0].dimensions["sample_count"] in {"1", "2"}
            assert incident.rule_versions == {
                "lte.erab.security-setup": "1.0.0"
            }
            trigger.to_data_part()

        metric = next(
            evidence
            for evidence in first[0].incident.evidence_refs
            if evidence.evidence_type.value == "METRIC"
        )
        assert "ERAB_SUCCESS_RATE_ABOVE_100_PERCENT" in metric.attributes[
            "quality_flags"
            ]
        assert first[0].incident.violated_kpis[0].observed_value == 90.0
        assert metric.attributes["facts"]["kpi.erab_success_rate"] == 90.0
        assert metric.attributes["total_sample_count"] == 3
        assert metric.attributes["valid_sample_count"] == 2
        assert metric.attributes["invalid_sample_count"] == 1
        assert metric.attributes["maximum"] == 90.0

        repository = DuckDbIncidentRepository(config)
        assert await repository.list() == ()

    _run(scenario())


@pytest.mark.parametrize("field_name", ("trace_id", "workflow_id"))
def test_scan_rejects_sensitive_envelope_ids_without_echo(
    local_config,
    field_name: str,
) -> None:
    async def scenario() -> None:
        detector = _detector(_detector_config(local_config))
        sensitive = "IMSI:310410000000001"
        kwargs = {
            "trace_id": "safe-trace",
            "workflow_id": "safe-workflow",
        }
        kwargs[field_name] = sensitive

        with pytest.raises(SensitiveDataError) as error:
            await detector.scan(**kwargs)
        assert sensitive not in str(error.value)
        assert "310410000000001" not in str(error.value)

    _run(scenario())


@pytest.mark.parametrize(
    "field_name",
    ("candidate_id", "trace_id", "idempotency_key", "actor", "reason"),
)
def test_confirm_rejects_sensitive_boundary_fields_without_echo(
    local_config,
    field_name: str,
) -> None:
    async def scenario() -> None:
        detector = _detector(_detector_config(local_config))
        sensitive = "IMSI:310410000000001"
        kwargs = {
            "candidate_id": "safe-candidate",
            "trace_id": "safe-trace",
            "idempotency_key": "safe-key",
            "actor": "safe-actor",
            "reason": "safe reason",
        }
        kwargs[field_name] = sensitive

        with pytest.raises(SensitiveDataError) as error:
            await detector.confirm(**kwargs)
        assert sensitive not in str(error.value)
        assert "310410000000001" not in str(error.value)

    _run(scenario())


def test_candidate_is_trace_scoped_but_incident_identity_is_data_stable(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        first = (await detector.scan("trace-a"))[0]
        other_trace = (await detector.scan("trace-b"))[0]

        assert first.message_id != other_trace.message_id
        assert first.incident_id == other_trace.incident_id
        assert first.incident.correlation_key == other_trace.incident.correlation_key
        assert first.incident.source_event_ids == other_trace.incident.source_event_ids

    _run(scenario())


def test_confirm_rescans_and_create_or_correlate_is_idempotent(local_config) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        candidate = (await detector.scan("trace-confirm"))[0]

        created = await detector.confirm(
            candidate.incident_id,
            trace_id="trace-confirm",
            idempotency_key="confirm-episode-one",
            actor="operator",
            reason="用户确认创建候选 Incident",
        )
        replay = await detector.confirm(
            candidate.incident_id,
            trace_id="trace-confirm",
            idempotency_key="confirm-episode-one",
            actor="operator",
            reason="用户确认创建候选 Incident",
        )

        assert replay == created
        assert created.incident_id == candidate.incident_id
        assert created.created_at == FIXED_NOW
        assert len(await DuckDbIncidentRepository(config).list()) == 1

    _run(scenario())


def test_confirm_rejects_candidate_that_no_longer_exists_after_rescan(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        candidate = (await detector.scan("trace-stale"))[0]

        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                """
                UPDATE performance
                SET ERAB_EstabInitSuccNbr_QCI1 = ERAB_EstabInitAttNbr_QCI1
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND measurement_end <= TIMESTAMPTZ '2025-11-20 00:30:00+00:00'
                """
            )

        with pytest.raises(ValueError, match="candidate"):
            await detector.confirm(
                candidate.incident_id,
                trace_id="trace-stale",
                idempotency_key="confirm-stale",
                actor="operator",
                reason="must rescan",
            )
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_candidate_id_binds_values_and_rejects_changed_abnormal_snapshot(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        candidate = (await detector.scan("trace-changed-value"))[0]

        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                """
                UPDATE performance
                SET ERAB_EstabInitSuccNbr_QCI1 = 91
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND ERAB_EstabInitSuccNbr_QCI1 = 90
                  AND measurement_end <= TIMESTAMPTZ '2025-11-20 00:30:00+00:00'
                """
            )

        rescanned = (await detector.scan("trace-changed-value"))[0]
        assert rescanned.incident_id != candidate.incident_id
        assert (
            rescanned.incident.violated_kpis[0].violation_id
            != candidate.incident.violated_kpis[0].violation_id
        )
        with pytest.raises(ValueError, match="candidate"):
            await detector.confirm(
                candidate.incident_id,
                trace_id="trace-changed-value",
                idempotency_key="confirm-changed-value",
                actor="operator",
                reason="must bind the previewed values",
            )
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_confirm_replay_precedes_changed_telemetry_but_new_key_rescans(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        candidate = (await detector.scan("trace-replay-order"))[0]
        created = await detector.confirm(
            candidate.incident_id,
            trace_id="trace-replay-order",
            idempotency_key="confirm-before-change",
            actor="operator",
            reason="confirmed original snapshot",
        )

        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                """
                UPDATE performance
                SET ERAB_EstabInitSuccNbr_QCI1 = 91
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND ERAB_EstabInitSuccNbr_QCI1 = 90
                  AND measurement_end <= TIMESTAMPTZ '2025-11-20 00:30:00+00:00'
                """
            )

        replay = await detector.confirm(
            candidate.incident_id,
            trace_id="trace-replay-order",
            idempotency_key="confirm-before-change",
            actor="operator",
            reason="confirmed original snapshot",
        )
        assert replay == created
        for overrides in (
            {"actor": "other-operator"},
            {"reason": "a different confirmation reason"},
            {"trace_id": "trace-replay-conflict"},
        ):
            metadata = {
                "trace_id": "trace-replay-order",
                "idempotency_key": "confirm-before-change",
                "actor": "operator",
                "reason": "confirmed original snapshot",
                **overrides,
            }
            with pytest.raises(IdempotencyConflictError):
                await detector.confirm(candidate.incident_id, **metadata)
        with pytest.raises(ValueError, match="candidate"):
            await detector.confirm(
                candidate.incident_id,
                trace_id="trace-replay-order",
                idempotency_key="confirm-after-change",
                actor="operator",
                reason="new requests must revalidate",
            )

        repository = DuckDbIncidentRepository(config)
        assert len(await repository.list()) == 1
        assert len(await repository.history(created.incident_id)) == 1

    _run(scenario())


def test_duplicate_source_keys_keep_unique_events_and_detector_can_scan(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO performance
                SELECT * FROM performance
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND measurement_end = TIMESTAMPTZ '2025-11-20 00:00:00+00:00'
                """
            )

        candidates = await _detector(config).scan("trace-duplicate-source-key")
        assert len(candidates) == 2
        first_events = candidates[0].incident.source_event_ids
        assert len(first_events) == 3
        assert len(set(first_events)) == 3

    _run(scenario())


def test_source_row_identity_and_candidate_ids_are_stable_across_reset(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)

        async def identities():
            observations = await DuckDbTelemetryRepository(config).query_kpis(
                kpi_names=("erab_success_rate",),
                technology=Technology.LTE,
            )
            candidates = await _detector(config).scan("trace-reset-stability")
            return (
                tuple(item.observation_id for item in observations),
                tuple(item.incident_id for item in candidates),
                tuple(
                    tuple(item.incident.source_event_ids) for item in candidates
                ),
            )

        first = await identities()
        initialize_database(config, reset=True)
        replay = await identities()
        assert replay == first

    _run(scenario())


@pytest.mark.parametrize("evidence_change", ("trace", "uplink_rssi"))
def test_candidate_id_binds_safe_evidence_snapshot(
    local_config,
    evidence_change: str,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        with duckdb.connect(str(config.database_path)) as connection:
            if evidence_change == "trace":
                connection.execute(
                    """
                    INSERT INTO cell_traces VALUES (
                        'RRC_SETUP',
                        TIMESTAMPTZ '2025-11-20 00:00:00+00:00',
                        TIMESTAMPTZ '2025-11-20 00:00:05+00:00',
                        '7', '700', 'SUCCESS'
                    )
                    """
                )

        detector = _detector(config)
        candidate = (await detector.scan(f"trace-evidence-{evidence_change}"))[0]

        with duckdb.connect(str(config.database_path)) as connection:
            if evidence_change == "trace":
                connection.execute(
                    """
                    UPDATE cell_traces
                    SET s1_sig_conn_setup_sig_conn_result = 'FAILURE'
                    WHERE start_enodeb_id = '7'
                      AND start_cell_id = '700'
                    """
                )
            else:
                connection.execute(
                    """
                    UPDATE performance
                    SET UL_RSSI = -101
                    WHERE CAST(enodeb_id AS VARCHAR) = '7'
                      AND CAST(cell_id AS VARCHAR) = '700'
                      AND measurement_end <= TIMESTAMPTZ '2025-11-20 00:30:00+00:00'
                    """
                )

        rescanned = (
            await detector.scan(f"trace-evidence-{evidence_change}")
        )[0]
        assert rescanned.incident.source_event_ids == candidate.incident.source_event_ids
        assert rescanned.incident_id != candidate.incident_id
        with pytest.raises(ValueError, match="candidate"):
            await detector.confirm(
                candidate.incident_id,
                trace_id=f"trace-evidence-{evidence_change}",
                idempotency_key=f"confirm-evidence-{evidence_change}",
                actor="operator",
                reason="evidence must match the preview",
            )
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_candidate_id_binds_full_rule_content_even_when_version_is_reused(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        candidate = (await detector.scan("trace-rule-content"))[0]

        rule_path = next(config.rules_dir.glob("*.json"))
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        rule["description_zh"] = "同版本文件被就地修改，必须成为不同候选。"
        rule_path.write_text(
            json.dumps(rule, ensure_ascii=False), encoding="utf-8"
        )

        rescanned = (await detector.scan("trace-rule-content"))[0]
        assert rescanned.incident.source_event_ids == candidate.incident.source_event_ids
        assert rescanned.incident_id != candidate.incident_id
        with pytest.raises(ValueError, match="candidate"):
            await detector.confirm(
                candidate.incident_id,
                trace_id="trace-rule-content",
                idempotency_key="confirm-old-rule-content",
                actor="operator",
                reason="rule content must match the preview",
            )

    _run(scenario())


def test_scan_fails_closed_before_building_an_oversized_episode(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                f"""
                INSERT INTO performance
                SELECT performance.*
                FROM performance, range({MAX_EPISODE_SAMPLES})
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND measurement_end =
                      TIMESTAMPTZ '2025-11-20 00:00:00+00:00'
                """
            )

        with pytest.raises(DetectorCapacityError, match="episode capacity"):
            await _detector(config).scan("trace-capacity-episode")
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_scan_fails_closed_when_json_candidate_response_would_exceed_cap(
    local_config,
) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        with duckdb.connect(str(config.database_path)) as connection:
            connection.execute(
                f"""
                INSERT INTO performance
                SELECT performance.* REPLACE (
                    CAST(8000 + i AS BIGINT) AS cell_id,
                    TIMESTAMPTZ '2025-12-01 00:00:00+00:00'
                        + i * INTERVAL '1 minute' AS measurement_end
                )
                FROM performance, range({MAX_SCAN_CANDIDATES + 1}) rows(i)
                WHERE CAST(enodeb_id AS VARCHAR) = '7'
                  AND CAST(cell_id AS VARCHAR) = '700'
                  AND measurement_end =
                      TIMESTAMPTZ '2025-11-20 00:00:00+00:00'
                """
            )

        with pytest.raises(DetectorCapacityError, match="candidate capacity"):
            await _detector(config).scan("trace-capacity-candidates")
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_scan_rejects_unbounded_current_rule_sets(local_config) -> None:
    async def scenario() -> None:
        config = _detector_config(local_config)
        for path in config.rules_dir.glob("*.json"):
            path.unlink()
        for index in range(MAX_CURRENT_RULES + 1):
            rule = _rule()
            rule["rule_id"] = f"lte.capacity.rule-{index:02d}"
            (config.rules_dir / f"rule-{index:02d}.json").write_text(
                json.dumps(rule, ensure_ascii=False), encoding="utf-8"
            )

        with pytest.raises(DetectorCapacityError, match="rule capacity"):
            await _detector(config).scan("trace-capacity-rules")
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())


def test_scan_fails_closed_when_metric_query_reaches_its_hard_limit(
    local_config,
) -> None:
    class SaturatedTelemetry:
        async def query_kpis(self, **_kwargs):
            return (None,) * MAX_QUERY_OBSERVATIONS

    async def scenario() -> None:
        config = _detector_config(local_config)
        detector = _detector(config)
        detector._telemetry = SaturatedTelemetry()

        with pytest.raises(DetectorCapacityError, match="observation capacity"):
            await detector.scan("trace-capacity-observations")
        assert await DuckDbIncidentRepository(config).list() == ()

    _run(scenario())
