from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "run_observability_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_observability_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
observability_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observability_demo)

HELPERS_PATH = Path(__file__).with_name("test_run_defense_demo.py")
HELPERS_SPEC = importlib.util.spec_from_file_location(
    "networkagent_defense_demo_test_helpers", HELPERS_PATH
)
assert HELPERS_SPEC is not None and HELPERS_SPEC.loader is not None
helpers = importlib.util.module_from_spec(HELPERS_SPEC)
HELPERS_SPEC.loader.exec_module(helpers)

FakeRunner = helpers.FakeRunner
INCIDENT_ID = helpers.INCIDENT_ID
RESOURCES = helpers.RESOURCES
_changed = helpers._changed

FIXED_NOW = datetime(2026, 8, 31, 2, 3, 4, tzinfo=UTC)
EXPECTED_GRAPH = [
    ("source_revision", "none", 1),
    ("source_cleanliness", "none", 1),
    ("preflight", "none", 1),
    ("workspace_init", "success", 1),
    ("workspace_status", "success", 1),
    ("governance_preview", "success", 1),
    ("approval_execute", "success", 1),
    ("terminal_verify", "success", 1),
    ("approval_execute", "success", 2),
    ("terminal_verify", "success", 2),
    ("workspace_init", "failure", 1),
    ("workspace_status", "failure", 1),
    ("governance_preview", "failure", 1),
    ("approval_execute", "failure", 1),
    ("terminal_verify", "failure", 1),
    ("approval_execute", "failure", 2),
    ("terminal_verify", "failure", 2),
    ("workspace_cleanup", "success", 1),
    ("workspace_cleanup", "failure", 1),
    ("source_revision", "none", 2),
    ("source_cleanliness", "none", 2),
    ("run_finalize", "none", 1),
]


class TickingClock:
    def __init__(self, step_ns: int = 1_000_000) -> None:
        self.value = 0
        self.step_ns = step_ns

    def __call__(self) -> int:
        current = self.value
        self.value += self.step_ns
        return current


def _invoke(
    tmp_path: Path,
    runner: FakeRunner,
    *arguments: str,
    stage_mapper=None,  # type: ignore[no-untyped-def]
    event_limit: int = 24,
    monotonic_ns=None,  # type: ignore[no-untyped-def]
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = observability_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        process_runner=runner,
        repository_root=tmp_path,
        utc_now=lambda: FIXED_NOW,
        monotonic_ns=monotonic_ns or TickingClock(),
        random_token=lambda: "0b5e7a123456",
        stage_mapper=stage_mapper,
        event_limit=event_limit,
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            item for key, child in value.items() for item in [key, *_strings(child)]
        ]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return []


def test_success_records_exact_bounded_graph_and_atomic_report(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()

    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 0 and error is None
    assert payload["schema"] == "networkagent-local-observability/1.0"
    assert payload["ok"] is True
    assert payload["source"]["commit_bound"] is True
    assert payload["source"]["commit_sha"] == "a" * 40
    assert payload["run"] == {
        "diagnostic_only": True,
        "duration_ms": 45,
        "error_class": "NONE",
        "error_code": None,
        "event_count": 22,
        "finished_at": "2026-08-31T02:03:04Z",
        "observation_id": "observation-0b5e7a123456",
        "started_at": "2026-08-31T02:03:04Z",
        "status": "PASS",
    }
    events = payload["events"]
    assert len(events) == 22
    assert [
        (item["stage"], item["branch"], item["attempt"]) for item in events
    ] == EXPECTED_GRAPH
    assert [item["sequence"] for item in events] == list(range(1, 23))
    assert all(
        set(item)
        == {
            "attempt",
            "branch",
            "duration_ms",
            "error_class",
            "outcome",
            "sequence",
            "stage",
        }
        for item in events
    )
    assert all(type(item["duration_ms"]) is int for item in events)
    assert all(item["duration_ms"] >= 0 for item in events)
    assert all(item["outcome"] == "SUCCEEDED" for item in events)
    assert all(item["error_class"] == "NONE" for item in events)

    assert payload["business_outcomes"] == {
        "cleanup": {"failure": True, "success": True},
        "exact_retry": {"failure": True, "success": True},
        "failure": {
            "closed_loop": False,
            "expected_business_result": True,
            "state": "REOPENED",
            "verification": "FAILED",
        },
        "success": {
            "closed_loop": True,
            "expected_business_result": True,
            "state": "RESOLVED",
            "verification": "PASSED",
        },
    }
    assert payload["timing_snapshot"]["sample_count"] == 1
    assert payload["timing_snapshot"]["diagnostic_only"] is True
    assert payload["timing_snapshot"]["wall_duration_ms"] == 45
    assert payload["timing_snapshot"]["instrumented_duration_ms"] == sum(
        item["duration_ms"] for item in events
    )
    assert payload["timing_snapshot"]["by_branch"]["success"] == {
        "duration_ms": 8,
        "event_count": 8,
    }
    assert payload["timing_snapshot"]["by_branch"]["failure"] == {
        "duration_ms": 8,
        "event_count": 8,
    }

    assert len(payload["local_alerts"]) == 4
    assert {item["name"] for item in payload["local_alerts"]} == {
        "LOCAL_CLEANUP_FAILURE",
        "LOCAL_CONTRACT_DRIFT",
        "LOCAL_EXECUTION_FAILURE",
        "LOCAL_RETRY_AMPLIFICATION",
    }
    assert all(item["state"] == "OK" for item in payload["local_alerts"])
    assert all(item["owner"] for item in payload["local_alerts"])
    assert all(item["threshold"] for item in payload["local_alerts"])
    assert all(item["runbook_anchor"] for item in payload["local_alerts"])

    assert payload["correlation"] == {
        "defense_report_sha256": payload["correlation"]["defense_report_sha256"],
        "observation_id": "observation-0b5e7a123456",
        "propagated_trace": False,
        "source_commit": "a" * 40,
    }
    assert len(payload["correlation"]["defense_report_sha256"]) == 64
    assert payload["privacy"]["status"] == "PASS"
    assert payload["privacy"]["high_cardinality_metric_labels"] is False
    assert payload["coverage"]["not_claimed"] == [
        "OPEN_TELEMETRY_EXPORT",
        "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
        "PROMETHEUS_METRICS",
        "EXTERNAL_ALERT_DELIVERY",
        "SERVICE_LEVEL_OBJECTIVES",
        "COLLECTOR_FAILURE_TOLERANCE",
        "GATE_E_OR_G5_CLOSURE",
        "CLOUD_OR_PRODUCTION_OBSERVABILITY",
    ]

    report = payload["report"]
    report_path = tmp_path / report["relative_path"]
    assert report_path.name == "local-observability-report.json"
    assert report_path.parent.name == "20260831T020304Z-0b5e7a123456"
    encoded = report_path.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == report["sha256"]
    persisted = json.loads(encoded)
    assert persisted == {
        key: value for key, value in payload.items() if key != "report"
    }
    assert not (report_path.parent / "success").exists()
    assert not (report_path.parent / "failure").exists()


def test_metrics_are_low_cardinality_and_private_values_are_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "https://private-proxy.invalid")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "private-credential.json")
    code, payload, error = _invoke(tmp_path, FakeRunner(), "--approve-local-simulation")

    assert code == 0 and error is None
    metrics = payload["metrics"]
    assert metrics["label_keys"] == [
        "branch",
        "error_class",
        "outcome",
        "stage",
    ]
    assert metrics["high_cardinality_labels_present"] is False
    assert metrics["series"]
    for series in metrics["series"]:
        assert series["name"] == "networkagent_local_stage"
        assert set(series["labels"]) == set(metrics["label_keys"])
        assert not {
            "trace_id",
            "incident_id",
            "resource_id",
            "action_hash",
            "observation_id",
            "commit_sha",
        }.intersection(series["labels"])
        assert type(series["event_count"]) is int
        assert type(series["duration_ms"]) is int

    strings = _strings(payload)
    for forbidden in (
        str(tmp_path),
        INCIDENT_ID,
        RESOURCES[0]["resource_id"],
        RESOURCES[1]["resource_id"],
        "b" * 64,
        "private-proxy.invalid",
        "private-credential.json",
    ):
        assert not any(forbidden in item for item in strings)


def test_high_cardinality_metric_label_is_rejected() -> None:
    with pytest.raises(observability_demo.ObservationError) as caught:
        observability_demo._validate_metric_labels(
            {
                "branch": "success",
                "error_class": "NONE",
                "outcome": "SUCCEEDED",
                "stage": "preflight",
                "trace_id": "private-trace",
            }
        )
    assert caught.value.code == "observation_contract_failed"


def test_near_match_or_untrusted_command_never_maps_to_fixed_graph(
    tmp_path: Path,
) -> None:
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T020304Z-0b5e7a123456"
    )
    evil = (
        sys.executable,
        str(tmp_path / "untrusted" / "local_stack.py"),
        "--workspace",
        str(tmp_path / "untrusted" / "success"),
        "evil",
        "--approve-action",
        "attacker",
    )
    extra = (
        sys.executable,
        str(tmp_path / "tools" / "local-stack" / "local_stack.py"),
        "--workspace",
        str(run_directory / "success"),
        "status",
        "extra",
    )

    assert (
        observability_demo._classify_command(
            evil,
            repository_root=tmp_path,
            run_directory=run_directory,
        )
        is None
    )
    assert (
        observability_demo._classify_command(
            extra,
            repository_root=tmp_path,
            run_directory=run_directory,
        )
        is None
    )


def test_child_failure_is_execution_class_and_does_not_echo_details(
    tmp_path: Path,
) -> None:
    class FailedStatus(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if arguments[-1] == "status":
                return subprocess.CompletedProcess(
                    arguments, 2, b"", b"private-child-detail"
                )
            return completed

    runner = FailedStatus()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 2 and payload is None
    assert error["error"]["class"] == "EXECUTION"
    assert error["error"]["code"] == "command_failed"
    assert "private" not in json.dumps(error)
    report_path = tmp_path / error["report"]["relative_path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run"]["error_class"] == "EXECUTION"
    assert any(
        item["outcome"] == "FAILED" and item["error_class"] == "EXECUTION"
        for item in report["events"]
    )
    assert not any("private" in item for item in _strings(report))


def test_contract_failure_is_classified_and_reported(tmp_path: Path) -> None:
    class InvalidDoctor(FakeRunner):
        def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
            completed = super().__call__(arguments, **kwargs)
            if arguments[-1] == "doctor":
                return _changed(
                    completed,
                    lambda body: body["report"].update({"demo_ready": False}),
                )
            return completed

    code, payload, error = _invoke(
        tmp_path, InvalidDoctor(), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"] == {
        "class": "CONTRACT",
        "code": "evidence_contract_failed",
        "message": "local observability demo detected contract drift",
    }
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    assert (
        next(
            item
            for item in report["local_alerts"]
            if item["name"] == "LOCAL_CONTRACT_DRIFT"
        )["state"]
        == "ALERT"
    )


def test_missing_defense_result_is_contract_failure_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = observability_demo.defense_demo._execute_demo

    def execute_without_result(**kwargs):  # type: ignore[no-untyped-def]
        original_execute(**kwargs)
        return None

    monkeypatch.setattr(
        observability_demo.defense_demo,
        "_execute_demo",
        execute_without_result,
    )
    runner = FakeRunner()

    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
    )

    assert code == 2 and payload is None
    assert error["error"] == {
        "class": "CONTRACT",
        "code": "evidence_contract_failed",
        "message": "local observability demo detected contract drift",
    }
    assert len([call for call in runner.calls if call[-2:] == ("reset", "--yes")]) == 2
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    assert report["ok"] is False
    assert report["run"]["status"] == "FAIL"
    assert report["run"]["error_class"] == "CONTRACT"
    assert report["events"][-1]["stage"] == "run_finalize"
    assert report["events"][-1]["outcome"] == "FAILED"


def test_cleanup_failure_is_classified_after_both_cleanup_attempts(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(cleanup_failure="failure")
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 2 and payload is None
    assert error["error"]["class"] == "CLEANUP"
    assert error["error"]["code"] == "cleanup_failed"
    resets = [call for call in runner.calls if call[-2:] == ("reset", "--yes")]
    assert len(resets) == 2
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    cleanup_alert = next(
        item
        for item in report["local_alerts"]
        if item["name"] == "LOCAL_CLEANUP_FAILURE"
    )
    assert cleanup_alert["state"] == "ALERT"


def test_observability_report_failure_is_artifact_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise observability_demo.ObservationError("report_write_failed")

    monkeypatch.setattr(observability_demo, "_write_report", fail_write)
    runner = FakeRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 2 and payload is None
    assert error == {
        "error": {
            "class": "ARTIFACT",
            "code": "report_write_failed",
            "message": ("local observability report could not be written safely"),
        },
        "ok": False,
        "schema": "networkagent-local-observability/1.0",
    }
    assert len([call for call in runner.calls if call[-2:] == ("reset", "--yes")]) == 2


def test_unknown_mapping_fails_observation_only_after_cleanup(
    tmp_path: Path,
) -> None:
    run_directory = (
        tmp_path / ".local" / "networkagent-defense" / "20260831T020304Z-0b5e7a123456"
    )

    def mapper(arguments):  # type: ignore[no-untyped-def]
        descriptor = observability_demo._classify_command(
            arguments,
            repository_root=tmp_path,
            run_directory=run_directory,
        )
        if descriptor == ("workspace_status", "success"):
            return None
        return descriptor

    runner = FakeRunner()
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
        stage_mapper=mapper,
    )

    assert code == 2 and payload is None
    assert error["error"]["class"] == "OBSERVATION"
    assert error["error"]["code"] == "observation_contract_failed"
    assert len([call for call in runner.calls if call[-2:] == ("reset", "--yes")]) == 2
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    assert report["run"]["event_count"] == 21
    assert len(report["events"]) <= 24
    assert (
        next(
            item
            for item in report["local_alerts"]
            if item["name"] == "LOCAL_CONTRACT_DRIFT"
        )["state"]
        == "ALERT"
    )


def test_event_budget_never_blocks_underlying_cleanup(tmp_path: Path) -> None:
    runner = FakeRunner()
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
        event_limit=5,
    )

    assert code == 2 and payload is None
    assert error["error"]["class"] == "OBSERVATION"
    assert len([call for call in runner.calls if call[-2:] == ("reset", "--yes")]) == 2
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    assert report["run"]["event_count"] == 5
    assert report["events"][-1]["stage"] == "run_finalize"


def test_finalize_clock_failure_is_observation_error_after_cleanup(
    tmp_path: Path,
) -> None:
    class FailingFinalizeClock:
        def __init__(self) -> None:
            self.calls = 0
            self.value = 0

        def __call__(self) -> int:
            self.calls += 1
            if self.calls == 44:
                raise RuntimeError("private clock detail")
            value = self.value
            self.value += 1_000_000
            return value

    runner = FakeRunner()
    code, payload, error = _invoke(
        tmp_path,
        runner,
        "--approve-local-simulation",
        monotonic_ns=FailingFinalizeClock(),
    )

    assert code == 2 and payload is None
    assert error["error"]["class"] == "OBSERVATION"
    assert error["error"]["code"] == "observation_contract_failed"
    assert "private" not in json.dumps(error)
    assert len([call for call in runner.calls if call[-2:] == ("reset", "--yes")]) == 2
    report = json.loads(
        (tmp_path / error["report"]["relative_path"]).read_text(encoding="utf-8")
    )
    assert report["run"]["status"] == "FAIL"
    assert report["events"][-1]["stage"] == "run_finalize"
    assert report["events"][-1]["outcome"] == "FAILED"
    assert report["events"][-1]["error_class"] == "OBSERVATION"


def test_report_publication_detects_temporary_file_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = tmp_path / ".local" / "networkagent-defense" / "fixed-run"
    run_directory.mkdir(parents=True)
    original_link = observability_demo.os.link

    def swap_before_link(source, target, **kwargs):  # type: ignore[no-untyped-def]
        Path(source).unlink()
        Path(source).write_bytes(b"attacker-controlled")
        return original_link(source, target, **kwargs)

    monkeypatch.setattr(observability_demo.os, "link", swap_before_link)

    with pytest.raises(observability_demo.ObservationError) as caught:
        observability_demo._write_report(
            tmp_path,
            run_directory,
            {"schema": "safe"},
            token="0b5e7a123456",
        )
    assert caught.value.code == "report_write_failed"


@pytest.mark.parametrize("arguments", [(), ("--approve-local-simulation", "extra")])
def test_cli_rejects_missing_confirmation_or_extra_arguments(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    code, payload, error = _invoke(tmp_path, FakeRunner(), *arguments)
    assert code == 2 and payload is None
    assert error["error"]["class"] == "INPUT"
    assert error["error"]["code"] in {
        "confirmation_required",
        "invalid_arguments",
    }


@pytest.mark.skipif(
    importlib.util.find_spec("duckdb") is None,
    reason="real Local Profile dependencies are not installed",
)
def test_real_runtime_observes_both_branches_and_cleans_workspaces() -> None:
    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--approve-local-simulation"],
        cwd=MODULE_PATH.parents[2],
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        check=False,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stderr == b""
    payload = json.loads(completed.stdout)
    assert payload["run"]["event_count"] == 22
    assert payload["business_outcomes"]["success"]["state"] == "RESOLVED"
    assert payload["business_outcomes"]["failure"]["state"] == "REOPENED"
    assert payload["business_outcomes"]["cleanup"] == {
        "failure": True,
        "success": True,
    }
    report_path = MODULE_PATH.parents[2] / payload["report"]["relative_path"]
    assert report_path.is_file()
    assert not (report_path.parent / "success").exists()
    assert not (report_path.parent / "failure").exists()
