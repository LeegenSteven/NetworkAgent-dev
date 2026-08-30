from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from io import StringIO
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "run_slo_evidence_demo.py"
SPEC = importlib.util.spec_from_file_location(
    "networkagent_slo_evidence_demo", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
slo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slo_demo)


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


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _source(*, clean: bool = True, sha: str = "a" * 40) -> dict[str, object]:
    return {
        "binding_stable": True,
        "commit_bound": clean,
        "commit_sha": sha,
        "git_available": True,
        "tracked_clean": clean,
    }


def _observation_body(*, clean: bool = True, sha: str = "a" * 40) -> dict[str, object]:
    events = [
        {
            "attempt": attempt,
            "branch": branch,
            "duration_ms": sequence,
            "error_class": "NONE",
            "outcome": "SUCCEEDED",
            "sequence": sequence,
            "stage": stage,
        }
        for sequence, (stage, branch, attempt) in enumerate(EXPECTED_GRAPH, start=1)
    ]
    alerts = [
        {
            "name": name,
            "owner": "networkagent-local-owner",
            "runbook_anchor": anchor,
            "state": "OK",
            "threshold": threshold,
        }
        for name, anchor, threshold in (
            (
                "LOCAL_EXECUTION_FAILURE",
                "local-observability-demo#execution-failure",
                "execution_error_count > 0",
            ),
            (
                "LOCAL_CLEANUP_FAILURE",
                "local-observability-demo#cleanup-failure",
                "cleanup_error_count > 0",
            ),
            (
                "LOCAL_RETRY_AMPLIFICATION",
                "local-observability-demo#retry-amplification",
                "exact_retry_proof != complete",
            ),
            (
                "LOCAL_CONTRACT_DRIFT",
                "local-observability-demo#contract-drift",
                "contract_or_observation_error_count > 0",
            ),
        )
    ]
    metric_aggregates: dict[tuple[str, str, str, str], list[int]] = {}
    for event in events:
        key = (
            event["branch"],
            event["error_class"],
            event["outcome"],
            event["stage"],
        )
        aggregate = metric_aggregates.setdefault(key, [0, 0])
        aggregate[0] += 1
        aggregate[1] += event["duration_ms"]
    metric_series = [
        {
            "duration_ms": metric_aggregates[key][1],
            "event_count": metric_aggregates[key][0],
            "labels": {
                "branch": key[0],
                "error_class": key[1],
                "outcome": key[2],
                "stage": key[3],
            },
            "name": "networkagent_local_stage",
        }
        for key in sorted(metric_aggregates)
    ]
    branch_timing = {
        branch: {
            "duration_ms": sum(
                item["duration_ms"] for item in events if item["branch"] == branch
            ),
            "event_count": sum(1 for item in events if item["branch"] == branch),
        }
        for branch in ("none", "success", "failure")
    }
    return {
        "business_outcomes": {
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
        },
        "correlation": {
            "defense_report_sha256": "b" * 64,
            "observation_id": "observation-0b5e7a123456",
            "propagated_trace": False,
            "source_commit": sha,
        },
        "coverage": {
            "delivered": [
                "BOUNDED_LOCAL_STAGE_EVENTS",
                "LOCAL_TIMING_SNAPSHOT",
                "STABLE_LOCAL_ERROR_CLASSIFICATION",
                "IN_REPORT_LOCAL_ALERT_EVALUATION",
            ],
            "not_claimed": [
                "OPEN_TELEMETRY_EXPORT",
                "CROSS_HTTP_REPLAY_A2A_MCP_TRACE",
                "PROMETHEUS_METRICS",
                "EXTERNAL_ALERT_DELIVERY",
                "SERVICE_LEVEL_OBJECTIVES",
                "COLLECTOR_FAILURE_TOLERANCE",
                "GATE_E_OR_G5_CLOSURE",
                "CLOUD_OR_PRODUCTION_OBSERVABILITY",
            ],
        },
        "events": events,
        "local_alerts": alerts,
        "metrics": {
            "high_cardinality_labels_present": False,
            "label_keys": ["branch", "error_class", "outcome", "stage"],
            "series": metric_series,
        },
        "ok": True,
        "privacy": {
            "absolute_paths_recorded": False,
            "child_stderr_recorded": False,
            "child_stdout_recorded": False,
            "environment_recorded": False,
            "high_cardinality_metric_labels": False,
            "raw_arguments_recorded": False,
            "status": "PASS",
        },
        "run": {
            "diagnostic_only": True,
            "duration_ms": 45,
            "error_class": "NONE",
            "error_code": None,
            "event_count": 22,
            "finished_at": "2026-08-31T02:03:04Z",
            "observation_id": "observation-0b5e7a123456",
            "started_at": "2026-08-31T02:03:04Z",
            "status": "PASS",
        },
        "schema": "networkagent-local-observability/1.0",
        "source": _source(clean=clean, sha=sha),
        "timing_snapshot": {
            "by_branch": branch_timing,
            "by_stage": {
                stage: {
                    "duration_ms": sum(
                        item["duration_ms"] for item in events if item["stage"] == stage
                    ),
                    "event_count": sum(1 for item in events if item["stage"] == stage),
                }
                for stage in {item[0] for item in EXPECTED_GRAPH}
            },
            "diagnostic_only": True,
            "instrumented_duration_ms": sum(item["duration_ms"] for item in events),
            "sample_count": 1,
            "wall_duration_ms": 45,
        },
    }


class FakeWindowRunner:
    def __init__(
        self,
        bodies: list[dict[str, object]] | None = None,
        *,
        tamper_digest_at: int | None = None,
        path_at: tuple[int, str] | None = None,
        run_offset: int = 0,
    ) -> None:
        self.bodies = bodies or [_observation_body() for _ in range(3)]
        self.tamper_digest_at = tamper_digest_at
        self.path_at = path_at
        self.run_offset = run_offset
        self.calls: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.run_directories: list[Path] = []

    def __call__(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        assert timeout == 240
        self.calls.append(arguments)
        self.environments.append(dict(env))
        index = len(self.calls) - 1
        assert arguments == (
            sys.executable,
            str(cwd / "tools" / "local-stack" / "run_observability_demo.py"),
            "--approve-local-simulation",
        )
        run_directory = (
            cwd
            / ".local"
            / "networkagent-defense"
            / (
                f"20260831T02030{index + self.run_offset + 1}Z-"
                f"{index + self.run_offset + 1:012x}"
            )
        )
        run_directory.mkdir(parents=True)
        self.run_directories.append(run_directory)
        body = deepcopy(self.bodies[index])
        encoded = _canonical(body)
        report_path = run_directory / "local-observability-report.json"
        report_path.write_bytes(encoded)
        relative_path = report_path.relative_to(cwd).as_posix()
        if self.path_at is not None and self.path_at[0] == index:
            relative_path = self.path_at[1]
        digest = hashlib.sha256(encoded).hexdigest()
        if self.tamper_digest_at == index:
            digest = "f" * 64
        payload = {
            **body,
            "report": {"relative_path": relative_path, "sha256": digest},
        }
        return subprocess.CompletedProcess(arguments, 0, _canonical(payload), b"")


class StepClock:
    def __init__(self, values: tuple[int, ...]) -> None:
        self.values = iter(values)

    def __call__(self) -> int:
        return next(self.values)


class TrustedChildFailureRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) != 1:
            return completed
        summary = json.loads(completed.stdout)
        body = {key: value for key, value in summary.items() if key != "report"}
        body["ok"] = False
        body["run"]["error_class"] = "EXECUTION"
        body["run"]["error_code"] = "command_failed"
        body["run"]["status"] = "FAIL"
        body["events"][-1]["error_class"] = "EXECUTION"
        body["events"][-1]["outcome"] = "FAILED"
        body["local_alerts"][0]["state"] = "ALERT"
        report_path = kwargs["cwd"] / summary["report"]["relative_path"]
        encoded = _canonical(body)
        report_path.write_bytes(encoded)
        summary["report"]["sha256"] = hashlib.sha256(encoded).hexdigest()
        return subprocess.CompletedProcess(
            arguments,
            2,
            b"",
            _canonical(
                {
                    "error": {
                        "class": "EXECUTION",
                        "code": "command_failed",
                        "message": "local observability demo command failed safely",
                    },
                    "ok": False,
                    "report": summary["report"],
                    "schema": "networkagent-local-observability/1.0",
                }
            ),
        )


class PassBodyFailureRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) != 1:
            return completed
        summary = json.loads(completed.stdout)
        return subprocess.CompletedProcess(
            arguments,
            2,
            b"",
            _canonical(
                {
                    "error": {
                        "class": "EXECUTION",
                        "code": "command_failed",
                        "message": "local observability demo command failed safely",
                    },
                    "ok": False,
                    "report": summary["report"],
                    "schema": "networkagent-local-observability/1.0",
                }
            ),
        )


class DuplicateChildJsonRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) != 1:
            return completed
        summary = json.loads(completed.stdout)
        report_path = kwargs["cwd"] / summary["report"]["relative_path"]
        encoded = b'{"schema":"one","schema":"two"}\n'
        report_path.write_bytes(encoded)
        summary["report"]["sha256"] = hashlib.sha256(encoded).hexdigest()
        return subprocess.CompletedProcess(arguments, 0, _canonical(summary), b"")


class ReusedChildReportRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) != 2:
            return completed
        summary = json.loads(completed.stdout)
        first_report = self.run_directories[0] / "local-observability-report.json"
        summary["report"] = {
            "relative_path": first_report.relative_to(kwargs["cwd"]).as_posix(),
            "sha256": hashlib.sha256(first_report.read_bytes()).hexdigest(),
        }
        return subprocess.CompletedProcess(arguments, 0, _canonical(summary), b"")


class PreexistingSloReportRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) == 3:
            (self.run_directories[-1] / "local-slo-report.json").write_bytes(
                b"attacker-controlled"
            )
        return completed


class LeakedWorkspaceRunner(FakeWindowRunner):
    def __call__(self, arguments, **kwargs):  # type: ignore[no-untyped-def]
        completed = super().__call__(arguments, **kwargs)
        if len(self.calls) == 1:
            (self.run_directories[-1] / "success").mkdir()
        return completed


def _invoke(
    tmp_path: Path,
    runner: object,
    *arguments: str,
    clock: object | None = None,
    token: str = "abc123def456",
) -> tuple[int, object | None, object | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = slo_demo.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        process_runner=runner,
        repository_root=tmp_path,
        monotonic_ns=clock
        or StepClock((0, 1_000_000, 2_000_000, 5_000_000, 6_000_000, 12_000_000)),
        random_token=lambda: token,
    )
    return (
        code,
        json.loads(stdout.getvalue()) if stdout.getvalue() else None,
        json.loads(stderr.getvalue()) if stderr.getvalue() else None,
    )


def _safe_windows() -> list[dict[str, object]]:
    return [
        {
            "duration_ms": value,
            "exact_retry_integrities": 2,
            "expected_branch_outcomes": 2,
            "local_alerts_ok": 4,
            "observation_contract_valid": True,
            "sequence": sequence,
            "stage_command_successes": 22,
            "workspace_cleanups": 2,
        }
        for sequence, value in enumerate((1, 3, 6), start=1)
    ]


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value))
    return set()


def test_happy_path_runs_three_distinct_windows_and_persists_exact_report(
    tmp_path: Path,
) -> None:
    runner = FakeWindowRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")

    assert code == 0 and error is None
    assert len(runner.calls) == 3
    assert len(set(runner.run_directories)) == 3
    assert payload["schema"] == "networkagent-local-slo-evidence/1.0"
    assert payload["classification"] == "LOCAL_DEMO_ACCEPTANCE_SLO_EVIDENCE"
    assert payload["ok"] is True
    assert payload["scope"] == {
        "execution_mode": "SEQUENTIAL",
        "isolated_run_directories": True,
        "latency_slo": False,
        "production_or_cloud_slo": False,
        "statistical_reliability_claim": False,
        "window_count": 3,
        "window_type": "FIXED_THREE_ISOLATED_RUN_ACCEPTANCE_WINDOW",
    }
    assert [item["duration_ms"] for item in payload["windows"]] == [1, 3, 6]
    assert all(
        item["stage_command_successes"] == 22
        and item["expected_branch_outcomes"] == 2
        and item["exact_retry_integrities"] == 2
        and item["workspace_cleanups"] == 2
        and item["local_alerts_ok"] == 4
        and item["observation_contract_valid"] is True
        for item in payload["windows"]
    )
    assert {key: value["denominator"] for key, value in payload["slis"].items()} == {
        "LOCAL_STAGE_COMMAND_SUCCESS": 66,
        "LOCAL_EXPECTED_BRANCH_OUTCOME": 6,
        "LOCAL_EXACT_RETRY_INTEGRITY": 6,
        "LOCAL_WORKSPACE_CLEANUP": 6,
        "LOCAL_OBSERVATION_CONTRACT_VALID": 3,
    }
    assert all(item["observed_ppm"] == 1_000_000 for item in payload["slis"].values())
    assert payload["evaluation"]["state"] == "OK"
    assert payload["evaluation"]["breached_slis"] == []
    assert payload["timing_snapshot"] == {
        "diagnostic_only": True,
        "max_duration_ms": 6,
        "median_duration_ms": 3,
        "min_duration_ms": 1,
        "sample_count": 3,
    }
    assert set(payload["report"]) == {"bytes", "filename", "sha256"}
    assert payload["report"]["filename"] == "local-slo-report.json"
    reports = list(
        (tmp_path / ".local" / "networkagent-defense").glob("*/local-slo-report.json")
    )
    assert len(reports) == 1
    encoded = reports[0].read_bytes()
    assert len(encoded) == payload["report"]["bytes"]
    assert hashlib.sha256(encoded).hexdigest() == payload["report"]["sha256"]
    persisted = {key: value for key, value in payload.items() if key != "report"}
    assert json.loads(encoded) == persisted
    assert set(persisted) == {
        "classification",
        "coverage",
        "evaluation",
        "ok",
        "privacy",
        "schema",
        "scope",
        "slis",
        "source",
        "timing_snapshot",
        "windows",
    }
    assert str(tmp_path) not in json.dumps(payload)
    assert _nested_keys(persisted).isdisjoint(
        {
            "incident_id",
            "workspace_id",
            "observation_id",
            "events",
            "metrics",
            "relative_path",
        }
    )
    assert all(
        "HTTPS_PROXY" not in environment
        and "GOOGLE_APPLICATION_CREDENTIALS" not in environment
        for environment in runner.environments
    )


@pytest.mark.parametrize(
    "arguments",
    [(), ("--approve-local-simulation", "extra"), ("--windows", "3")],
)
def test_cli_accepts_only_the_single_approval_flag(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    runner = FakeWindowRunner()
    code, payload, error = _invoke(tmp_path, runner, *arguments)
    assert code == 2 and payload is None
    assert error["error"]["code"] in {"confirmation_required", "invalid_arguments"}
    assert runner.calls == []


@pytest.mark.parametrize(
    ("sli", "field", "value", "observed"),
    [
        ("LOCAL_STAGE_COMMAND_SUCCESS", "stage_command_successes", 21, 984_848),
        ("LOCAL_EXPECTED_BRANCH_OUTCOME", "expected_branch_outcomes", 1, 833_333),
        ("LOCAL_EXACT_RETRY_INTEGRITY", "exact_retry_integrities", 1, 833_333),
        ("LOCAL_WORKSPACE_CLEANUP", "workspace_cleanups", 1, 833_333),
        (
            "LOCAL_OBSERVATION_CONTRACT_VALID",
            "observation_contract_valid",
            False,
            666_666,
        ),
    ],
)
def test_each_single_miss_is_a_precise_breach(
    sli: str, field: str, value: object, observed: int
) -> None:
    windows = _safe_windows()
    windows[0][field] = value
    slis, evaluation = slo_demo._evaluate_windows(windows)
    assert slis[sli]["observed_ppm"] == observed
    assert slis[sli]["state"] == "BREACH"
    assert evaluation["breached_slis"] == [sli]
    assert evaluation["state"] == "BREACH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence", True),
        ("duration_ms", False),
        ("stage_command_successes", True),
        ("expected_branch_outcomes", 2.0),
        ("exact_retry_integrities", "2"),
        ("workspace_cleanups", -1),
        ("local_alerts_ok", 5),
        ("observation_contract_valid", 1),
    ],
)
def test_window_values_are_type_strict_and_bounded(field: str, value: object) -> None:
    windows = _safe_windows()
    windows[0][field] = value
    with pytest.raises(slo_demo.SloEvidenceError) as caught:
        slo_demo._evaluate_windows(windows)
    assert caught.value.code == "window_contract_failed"


def test_window_sequence_and_key_set_are_exact() -> None:
    windows = _safe_windows()
    windows[1]["sequence"] = 1
    with pytest.raises(slo_demo.SloEvidenceError):
        slo_demo._evaluate_windows(windows)

    windows = _safe_windows()
    windows[0]["private"] = 1
    with pytest.raises(slo_demo.SloEvidenceError):
        slo_demo._evaluate_windows(windows)


@pytest.mark.parametrize(
    "windows",
    [[], _safe_windows()[:2], [*_safe_windows(), _safe_windows()[0]]],
)
def test_exactly_three_windows_are_required(windows: list[dict[str, object]]) -> None:
    with pytest.raises(slo_demo.SloEvidenceError):
        slo_demo._evaluate_windows(windows)


def test_normal_reopened_failed_branch_counts_as_expected_outcome() -> None:
    body = _observation_body()
    window = slo_demo._normalize_observation(body, duration_ms=0)
    assert window["expected_branch_outcomes"] == 2
    assert window["observation_contract_valid"] is True


def test_business_or_alert_extra_data_invalidates_observation_contract() -> None:
    body = _observation_body()
    body["business_outcomes"]["cleanup"]["private"] = True  # type: ignore[index]
    window = slo_demo._normalize_observation(body, duration_ms=0)
    assert window["workspace_cleanups"] == 2
    assert window["observation_contract_valid"] is False

    body = _observation_body()
    body["local_alerts"][0]["state"] = "ALERT"  # type: ignore[index]
    window = slo_demo._normalize_observation(body, duration_ms=0)
    assert window["local_alerts_ok"] == 3
    assert window["observation_contract_valid"] is False


def test_metric_or_timing_drift_invalidates_observation_contract() -> None:
    body = _observation_body()
    body["metrics"]["series"].append(  # type: ignore[index,union-attr]
        deepcopy(body["metrics"]["series"][0])  # type: ignore[index]
    )
    window = slo_demo._normalize_observation(body, duration_ms=0)
    assert window["observation_contract_valid"] is False

    body = _observation_body()
    body["timing_snapshot"]["by_branch"]["success"][  # type: ignore[index]
        "duration_ms"
    ] += 1
    window = slo_demo._normalize_observation(body, duration_ms=0)
    assert window["observation_contract_valid"] is False


def test_dirty_but_stable_source_is_worktree_evidence(tmp_path: Path) -> None:
    runner = FakeWindowRunner([_observation_body(clean=False) for _ in range(3)])
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 0 and error is None
    assert payload["classification"] == "LOCAL_WORKTREE_DEMO_ACCEPTANCE_SLO_EVIDENCE"
    assert payload["source"]["commit_bound"] is False
    assert payload["ok"] is True


def test_source_sha_drift_is_error_not_breach(tmp_path: Path) -> None:
    bodies = [_observation_body(), _observation_body(), _observation_body(sha="c" * 40)]
    code, payload, error = _invoke(
        tmp_path, FakeWindowRunner(bodies), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "source_binding_failed"
    assert not list(tmp_path.rglob("local-slo-report.json"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_stable", False),
        ("git_available", False),
        ("commit_sha", None),
        ("commit_sha", "not-a-sha"),
        ("tracked_clean", 1),
        ("commit_bound", 1),
    ],
)
def test_invalid_source_is_error_not_breach(
    tmp_path: Path, field: str, value: object
) -> None:
    bodies = [_observation_body() for _ in range(3)]
    bodies[0]["source"][field] = value  # type: ignore[index]
    code, payload, error = _invoke(
        tmp_path, FakeWindowRunner(bodies), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "source_binding_failed"
    assert not list(tmp_path.rglob("local-slo-report.json"))


def test_trusted_semantic_miss_runs_all_windows_and_persists_breach(
    tmp_path: Path,
) -> None:
    bodies = [_observation_body() for _ in range(3)]
    bodies[0]["business_outcomes"]["cleanup"]["failure"] = False  # type: ignore[index]
    runner = FakeWindowRunner(bodies)
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert len(runner.calls) == 3
    assert error["error"]["code"] == "slo_breach"
    assert set(error["report"]) == {"bytes", "filename", "sha256"}
    report_path = list(tmp_path.rglob("local-slo-report.json"))[0]
    report = json.loads(report_path.read_bytes())
    assert report["ok"] is False
    assert report["slis"]["LOCAL_WORKSPACE_CLEANUP"]["numerator"] == 5
    assert report["slis"]["LOCAL_WORKSPACE_CLEANUP"]["observed_ppm"] == 833_333
    assert report["evaluation"]["state"] == "BREACH"


def test_trusted_child_nonzero_exit_is_measurable_breach_and_runs_all_windows(
    tmp_path: Path,
) -> None:
    runner = TrustedChildFailureRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert len(runner.calls) == 3
    assert error["error"]["code"] == "slo_breach"
    report = json.loads(list(tmp_path.rglob("local-slo-report.json"))[0].read_bytes())
    assert report["slis"]["LOCAL_OBSERVATION_CONTRACT_VALID"] == {
        "denominator": 3,
        "error_budget_ppm": 0,
        "numerator": 2,
        "objective_ppm": 1_000_000,
        "observed_ppm": 666_666,
        "state": "BREACH",
    }
    assert report["slis"]["LOCAL_STAGE_COMMAND_SUCCESS"]["numerator"] == 65
    assert report["slis"]["LOCAL_STAGE_COMMAND_SUCCESS"]["state"] == "BREACH"
    assert report["slis"]["LOCAL_EXPECTED_BRANCH_OUTCOME"]["state"] == "OK"
    assert report["slis"]["LOCAL_EXACT_RETRY_INTEGRITY"]["state"] == "OK"
    assert report["slis"]["LOCAL_WORKSPACE_CLEANUP"]["state"] == "OK"


def test_nonzero_envelope_cannot_relabel_a_pass_report_as_a_window(
    tmp_path: Path,
) -> None:
    runner = PassBodyFailureRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert len(runner.calls) == 1
    assert error["error"]["code"] == "window_contract_failed"
    assert not list(tmp_path.rglob("local-slo-report.json"))


def test_fresh_three_window_set_can_recover_without_mutating_old_breach(
    tmp_path: Path,
) -> None:
    bad = [_observation_body() for _ in range(3)]
    bad[0]["events"][0]["outcome"] = "FAILED"  # type: ignore[index]
    first = FakeWindowRunner(bad)
    code, _, error = _invoke(
        tmp_path, first, "--approve-local-simulation", token="111111111111"
    )
    assert code == 2 and error["error"]["code"] == "slo_breach"
    old_path = list(tmp_path.rglob("local-slo-report.json"))[0]
    old_bytes = old_path.read_bytes()
    old_sha = hashlib.sha256(old_bytes).hexdigest()

    second = FakeWindowRunner(run_offset=3)
    code, payload, error = _invoke(
        tmp_path,
        second,
        "--approve-local-simulation",
        token="222222222222",
    )
    assert code == 0 and error is None and payload["ok"] is True
    assert old_path.read_bytes() == old_bytes
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == old_sha


@pytest.mark.parametrize(
    ("runner", "code"),
    [
        (FakeWindowRunner(tamper_digest_at=1), "window_contract_failed"),
        (
            FakeWindowRunner(path_at=(1, "../private/local-observability-report.json")),
            "window_contract_failed",
        ),
    ],
)
def test_untrusted_child_report_is_error_without_slo_math(
    tmp_path: Path, runner: FakeWindowRunner, code: str
) -> None:
    exit_code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert exit_code == 2 and payload is None
    assert error["error"]["code"] == code
    assert not list(tmp_path.rglob("local-slo-report.json"))


@pytest.mark.parametrize(
    "runner",
    [DuplicateChildJsonRunner(), ReusedChildReportRunner()],
)
def test_duplicate_json_or_reused_run_is_untrusted(
    tmp_path: Path, runner: FakeWindowRunner
) -> None:
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert error["error"]["code"] == "window_contract_failed"
    assert not list(tmp_path.rglob("local-slo-report.json"))


@pytest.mark.parametrize(
    "body",
    [
        b'{"value":NaN}',
        b"\xff",
        b"[1,2,3]",
        b" " * (64 * 1024 + 1),
    ],
    ids=("nonfinite", "utf8", "non-object", "oversize"),
)
def test_child_json_boundary_rejects_unsafe_documents(body: bytes) -> None:
    with pytest.raises(slo_demo.SloEvidenceError) as caught:
        slo_demo._decode_json_document(body)
    assert caught.value.code == "window_contract_failed"


def test_private_runner_exception_is_redacted(tmp_path: Path) -> None:
    class PrivateFailure:
        def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("private incident_id and absolute path")

    code, payload, error = _invoke(
        tmp_path, PrivateFailure(), "--approve-local-simulation"
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "window_execution_failed"
    assert "private" not in json.dumps(error)


def test_preexisting_final_report_fails_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    runner = PreexistingSloReportRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert error["error"]["code"] == "report_write_failed"
    final = runner.run_directories[-1] / "local-slo-report.json"
    assert final.read_bytes() == b"attacker-controlled"


def test_claimed_cleanup_rejects_a_remaining_workspace(tmp_path: Path) -> None:
    runner = LeakedWorkspaceRunner()
    code, payload, error = _invoke(tmp_path, runner, "--approve-local-simulation")
    assert code == 2 and payload is None
    assert len(runner.calls) == 1
    assert error["error"]["code"] == "window_contract_failed"
    assert not list(tmp_path.rglob("local-slo-report.json"))


def test_report_writer_requires_the_captured_run_directory_chain(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / ".local"
    defense_root = local_root / "networkagent-defense"
    run_directory = defense_root / "20260831T020301Z-000000000001"
    run_directory.mkdir(parents=True)
    chain = tuple(
        slo_demo._directory_identity(path)
        for path in (local_root, defense_root, run_directory)
    )
    wrong_chain = (chain[0], chain[1], (chain[2][0], chain[2][1] + 1))
    with pytest.raises(slo_demo.SloEvidenceError) as caught:
        slo_demo._write_report(
            tmp_path,
            run_directory,
            {"schema": "safe"},
            expected_chain=wrong_chain,
            token="abc123def456",
        )
    assert caught.value.code == "report_write_failed"
    assert not (run_directory / "local-slo-report.json").exists()


def test_invalid_report_token_never_publishes_partial_slo_report(
    tmp_path: Path,
) -> None:
    code, payload, error = _invoke(
        tmp_path,
        FakeWindowRunner(),
        "--approve-local-simulation",
        token="private-token",
    )
    assert code == 2 and payload is None
    assert error["error"]["code"] == "report_write_failed"
    assert "private" not in json.dumps(error)
    assert not list(tmp_path.rglob("local-slo-report.json"))


def test_timing_is_diagnostic_and_never_changes_sli() -> None:
    windows = _safe_windows()
    slis_before, _ = slo_demo._evaluate_windows(windows)
    windows[0]["duration_ms"] = 999_999
    slis_after, _ = slo_demo._evaluate_windows(windows)
    assert slis_after == slis_before
    assert slo_demo._timing_snapshot(windows) == {
        "diagnostic_only": True,
        "max_duration_ms": 999_999,
        "median_duration_ms": 6,
        "min_duration_ms": 3,
        "sample_count": 3,
    }
