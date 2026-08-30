from __future__ import annotations

import csv
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

import telco_lab.replay as replay_module
from telco_lab.adapters import (
    BUBBLERAN_CSV_ADAPTER_ID,
    BUBBLERAN_DATASET_ID,
    BUBBLERAN_DATASET_VERSION,
    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
    BUBBLERAN_SOURCE_LICENSE,
    adapt_bubbleran_persistent_interference_csv,
)
from telco_lab.catalog import FixtureCatalogProvider
from telco_lab.downloader import DownloadReceipt
from telco_lab.replay import (
    ReplayError,
    ReplayEvent,
    ReplayPlan,
    ReplayPolicy,
    build_replay_plan,
    validate_replay_environment,
)
from telco_lab.schema import (
    LabBundle,
    LabBundleManifest,
    LabObservation,
    canonical_json_bytes,
    compute_bundle_content_sha256,
    stable_content_id,
)
from telco_lab.workspace import TelcoLab


SOURCE_START = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
REPLAY_START = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
RESOURCE_ID = "bubbleran.persistent-interference.anomalous.v1"


def _csv_bytes(
    *,
    offsets: tuple[int, ...] = (0, 1, 2),
    labels: tuple[bool, ...] | None = None,
    missing_metric: bool = False,
) -> bytes:
    labels = labels or tuple(index == 1 for index in range(len(offsets)))
    if len(labels) != len(offsets):
        raise AssertionError("fixture labels and offsets must align")
    headers = [
        "",
        "timestamp",
        "ran_ue_id",
        "e2node_nb_id",
        *BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
        "timestamp_iso",
        "persistent_anomaly",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row_index, (offset, label) in enumerate(zip(offsets, labels)):
        instant = SOURCE_START + timedelta(seconds=offset)
        row: dict[str, object] = {
            "": row_index,
            "timestamp": int(instant.timestamp()),
            "ran_ue_id": "answer-key-source-identifier",
            "e2node_nb_id": "50",
            "timestamp_iso": instant.replace(tzinfo=None).isoformat(),
            "persistent_anomaly": str(label),
        }
        row.update(
            {
                name: f"{row_index + metric_index / 100:.2f}"
                for metric_index, name in enumerate(
                    BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP,
                    start=1,
                )
            }
        )
        if missing_metric and row_index == 0:
            row["mac_ul_bler"] = ""
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _catalog(body: bytes) -> FixtureCatalogProvider:
    return FixtureCatalogProvider(
        {
            "schema_version": "1.0",
            "catalog_id": "replay-fixture",
            "catalog_version": "1.0.0",
            "resources": [
                {
                    "resource_id": RESOURCE_ID,
                    "dataset_id": BUBBLERAN_DATASET_ID,
                    "dataset_version": BUBBLERAN_DATASET_VERSION,
                    "filename": "anomalous.csv",
                    "source_url": "https://fixtures.example.test/anomalous.csv",
                    "allowed_hosts": ["fixtures.example.test"],
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "media_type": "text/csv",
                    "adapter": BUBBLERAN_CSV_ADAPTER_ID,
                    "license": {
                        "id": BUBBLERAN_SOURCE_LICENSE,
                        "name": "Creative Commons Attribution-ShareAlike 4.0",
                        "url": "https://creativecommons.org/licenses/by-sa/4.0/",
                        "evidence_url": "https://fixtures.example.test/LICENSE",
                        "evidence_sha256": "a" * 64,
                        "attribution": "BubbleRAN dataset authors",
                        "reviewed_at": "2026-08-30",
                        "acceptance_required": True,
                    },
                }
            ],
        }
    )


class _MemoryDownloader:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def download(self, resource, target: Path) -> DownloadReceipt:
        self.calls += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.body)
        return DownloadReceipt(
            resource_id=resource.resource_id,
            filename=resource.filename,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            cached=False,
        )


@dataclass(frozen=True, slots=True)
class _ReplaySource:
    lab: TelcoLab
    bundle: LabBundle
    provider: FixtureCatalogProvider
    body: bytes
    artifact_path: Path


def _source(
    workspace: Path,
    *,
    offsets: tuple[int, ...] = (0, 1, 2),
    labels: tuple[bool, ...] | None = None,
    missing_metric: bool = False,
) -> _ReplaySource:
    body = _csv_bytes(
        offsets=offsets,
        labels=labels,
        missing_metric=missing_metric,
    )
    provider = _catalog(body)
    downloader = _MemoryDownloader(body)
    lab = TelcoLab(provider, workspace, downloader=downloader)  # type: ignore[arg-type]
    artifact = lab.fetch(
        RESOURCE_ID,
        accepted_license=BUBBLERAN_SOURCE_LICENSE,
    )
    bundle = adapt_bubbleran_persistent_interference_csv(artifact.local_path)
    assert downloader.calls == 1
    return _ReplaySource(lab, bundle, provider, body, artifact.local_path)


@pytest.fixture
def replay_source(tmp_path: Path) -> _ReplaySource:
    return _source(tmp_path)


def _policy(**changes: object) -> ReplayPolicy:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:9080/v1/faults/replay",
        "action_mode": "disabled",
        "speed": 2,
        "max_events": 100,
        "max_rate_per_second": 10,
        "max_duration_seconds": 60,
        "max_payload_bytes": 4096,
        "max_total_payload_bytes": 32_768,
        "max_resources": 10,
        "max_concurrency": 1,
    }
    values.update(changes)
    return ReplayPolicy(**values)


def _plan(
    source: _ReplaySource,
    *,
    bundle: LabBundle | None = None,
    policy: ReplayPolicy | None = None,
) -> ReplayPlan:
    return build_replay_plan(
        source.lab,
        bundle or source.bundle,
        scenario="detector-demo",
        replay_window_start=REPLAY_START,
        policy=policy or _policy(),
        environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"},
    )


def _forge_observation_bundle(
    bundle: LabBundle,
    *,
    metric_name: str | None = None,
    unit: str | None = None,
    quality_flags: tuple[str, ...] | None = None,
    value_delta: float = 0.0,
) -> LabBundle:
    original = bundle.observations[0]
    metrics = dict(original.metrics)
    units = dict(original.units)
    if metric_name is not None:
        metrics = {metric_name: next(iter(metrics.values()))}
        units = {metric_name: unit or "count"}
    else:
        first_name = next(iter(metrics))
        metrics[first_name] += value_delta
        if unit is not None:
            units[first_name] = unit

    observation_payload = original.model_dump(mode="python")
    observation_payload.update(
        {
            "metrics": metrics,
            "units": units,
            "quality_flags": (
                original.quality_flags if quality_flags is None else quality_flags
            ),
        }
    )
    identity = {
        key: value
        for key, value in observation_payload.items()
        if key not in {"schema_version", "observation_id"}
    }
    observation_payload["observation_id"] = stable_content_id("obs", identity)
    replacement = LabObservation.model_validate(observation_payload)
    observations = (replacement, *bundle.observations[1:])

    manifest_payload = bundle.manifest.identity_payload()
    manifest_payload.update(
        {
            "content_sha256": compute_bundle_content_sha256(
                observations,
                bundle.ground_truth_episodes,
            ),
            "metric_names": tuple(
                sorted({name for item in observations for name in item.metrics})
            ),
        }
    )
    manifest = LabBundleManifest(
        bundle_id=stable_content_id("bundle", manifest_payload),
        **manifest_payload,
    )
    return LabBundle(
        manifest=manifest,
        observations=observations,
        ground_truth_episodes=bundle.ground_truth_episodes,
    )


def test_plan_is_deterministic_shifts_utc_time_and_excludes_answer_keys(
    replay_source: _ReplaySource,
) -> None:
    first = _plan(replay_source)
    second = _plan(replay_source)

    assert first == second
    assert ReplayPlan.model_validate(first.model_dump(mode="python")) == first
    assert first.plan_id.startswith("labreplay-")
    assert [item.sequence_number for item in first.events] == [1, 2, 3]
    assert [item.source_observed_at for item in first.events] == [
        SOURCE_START,
        SOURCE_START + timedelta(seconds=1),
        SOURCE_START + timedelta(seconds=2),
    ]
    assert [item.replay_observed_at for item in first.events] == [
        REPLAY_START,
        REPLAY_START + timedelta(seconds=1),
        REPLAY_START + timedelta(seconds=2),
    ]
    assert [item.scheduled_offset_seconds for item in first.events] == [0, 0.5, 1]
    assert len({item.source_event_id for item in first.events}) == 3
    assert len({item.idempotency_key for item in first.events}) == 3

    wire = first.model_dump(mode="json")
    assert "ground_truth" not in str(wire).lower()
    assert "persistent_interference" not in str(wire)
    assert "answer-key-source-identifier" not in str(wire)
    assert "label" not in ReplayEvent.model_fields
    assert first.events[0].metrics == replay_source.bundle.observations[0].metrics
    assert first.events[0].units == replay_source.bundle.observations[0].units


def test_delivery_controls_make_duplicate_out_of_order_and_resume_testable(
    replay_source: _ReplaySource,
) -> None:
    plan = _plan(replay_source)
    delivery = plan.delivery_order((3, 1, 1, 2))

    assert [item.sequence_number for item in delivery] == [3, 1, 1, 2]
    assert delivery[1].source_event_id == delivery[2].source_event_id
    assert delivery[1].idempotency_key == delivery[2].idempotency_key
    assert plan.resume_after(1) == plan.events[1:]
    assert plan.resume_after(3) == ()

    with pytest.raises(ReplayError) as unknown:
        plan.delivery_order((4,))
    assert unknown.value.code == "replay_sequence_invalid"
    with pytest.raises(ReplayError):
        plan.resume_after(-1)

    consumed = False

    def unbounded():
        nonlocal consumed
        consumed = True
        while True:
            yield 1

    with pytest.raises(ReplayError):
        plan.delivery_order(unbounded())  # type: ignore[arg-type]
    assert consumed is False


def test_delivery_rejects_a_sequence_that_lies_about_its_bounded_length(
    replay_source: _ReplaySource,
) -> None:
    plan = _plan(replay_source)

    class _DishonestSequence(Sequence[int]):
        def __init__(self) -> None:
            self.item_reads = 0

        def __len__(self) -> int:
            return 1

        def __getitem__(self, index):
            self.item_reads += 1
            if isinstance(index, slice):
                return self
            if index < 50_000:
                return 1
            raise IndexError

    dishonest = _DishonestSequence()
    with pytest.raises(ReplayError) as caught:
        plan.delivery_order(dishonest)

    assert caught.value.code == "replay_sequence_invalid"
    assert dishonest.item_reads == 0


def test_duplicate_delivery_cannot_exceed_total_payload_budget(
    replay_source: _ReplaySource,
) -> None:
    first = _plan(replay_source)
    payload_sizes = tuple(
        len(canonical_json_bytes(event.sink_payload())) for event in first.events
    )
    budget = sum(payload_sizes)
    bounded = _plan(
        replay_source,
        policy=_policy(max_total_payload_bytes=budget),
    )
    largest_sequence = max(
        range(1, len(payload_sizes) + 1),
        key=lambda sequence: payload_sizes[sequence - 1],
    )
    repeat_count = budget // payload_sizes[largest_sequence - 1] + 1

    with pytest.raises(ReplayError) as caught:
        bounded.delivery_order((largest_sequence,) * repeat_count)
    assert caught.value.code == "replay_payload_limit"


def test_public_plan_boundaries_revalidate_policy_events_and_plan_identity(
    replay_source: _ReplaySource,
) -> None:
    plan = _plan(replay_source)
    first_payload_bytes = len(canonical_json_bytes(plan.events[0].sink_payload()))
    repeat_count = plan.policy.max_total_payload_bytes // first_payload_bytes + 1
    oversized_order = (1,) * repeat_count
    assert repeat_count <= plan.policy.max_events

    with pytest.raises(ReplayError) as original_budget:
        plan.delivery_order(oversized_order)
    assert original_budget.value.code == "replay_payload_limit"

    inflated_policy = plan.policy.model_copy(
        update={
            "max_events": 1_000_000_000,
            "max_total_payload_bytes": 1_000_000_000_000,
        }
    )
    bad_event = plan.events[0].model_copy(update={"payload_sha256": "0" * 64})

    class _DishonestFlags(Sequence[str]):
        def __init__(self) -> None:
            self.item_reads = 0

        def __len__(self) -> int:
            return 0

        def __getitem__(self, index):
            self.item_reads += 1
            return "MISSING_METRIC_VALUES"

    dishonest_flags = _DishonestFlags()
    unbounded_event = plan.events[0].model_copy(
        update={"quality_flags": dishonest_flags}
    )
    attacks = (
        plan.model_copy(update={"policy": inflated_policy}),
        plan.model_copy(update={"events": (bad_event, *plan.events[1:])}),
        plan.model_copy(update={"plan_id": "labreplay-" + "0" * 64}),
        plan.model_copy(update={"events": (unbounded_event, *plan.events[1:])}),
    )
    for compromised in attacks:
        with pytest.raises(ReplayError) as delivery_error:
            compromised.delivery_order((1,))
        assert delivery_error.value.code == "replay_plan_invalid"

        with pytest.raises(ReplayError) as resume_error:
            compromised.resume_after(0)
        assert resume_error.value.code == "replay_plan_invalid"

    with pytest.raises(ReplayError) as inflated_budget:
        attacks[0].delivery_order(oversized_order)
    assert inflated_budget.value.code == "replay_plan_invalid"
    assert dishonest_flags.item_reads == 0


def test_event_mappings_are_unbypassably_immutable_and_serialization_is_stable(
    replay_source: _ReplaySource,
) -> None:
    event = _plan(replay_source).events[0]
    before_payload = event.sink_payload()
    before_hash = event.payload_sha256
    before_wire = canonical_json_bytes(event)

    with pytest.raises(TypeError):
        event.metrics["ran.mac.ul_bler"] = 99.0  # type: ignore[index]
    with pytest.raises(TypeError):
        event.units.update({"ran.mac.ul_bler": "percent"})  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        dict.__setitem__(event.metrics, "persistent_interference", 1.0)  # type: ignore[arg-type]

    assert not isinstance(event.metrics, dict)
    assert event.sink_payload() == before_payload
    assert event.payload_sha256 == before_hash
    assert canonical_json_bytes(event) == before_wire
    assert ReplayEvent.model_validate(event.model_dump(mode="python")) == event
    assert '"ran.mac.ul_bler"' in event.model_dump_json()
    assert "persistent_interference" not in str(event.sink_payload())


def test_sink_payload_revalidates_ids_checksum_and_label_safe_projection(
    replay_source: _ReplaySource,
) -> None:
    event = _plan(replay_source).events[0]
    attacks = (
        event.model_copy(update={"source_event_id": "labevent-" + "0" * 64}),
        event.model_copy(update={"payload_sha256": "0" * 64}),
        event.model_copy(
            update={
                "metrics": {"persistent_interference": 1.0},
                "units": {"persistent_interference": "count"},
            }
        ),
        event.model_copy(update={"quality_flags": ("persistent_interference",)}),
    )
    for compromised in attacks:
        with pytest.raises(ReplayError) as caught:
            compromised.sink_payload()
        assert caught.value.code == "replay_bundle_invalid"
        assert "persistent_interference" not in str(caught.value)


def test_workspace_must_be_a_real_fully_verified_telco_lab(
    replay_source: _ReplaySource,
    tmp_path: Path,
) -> None:
    class _DuckTypedVerifiedLab:
        def verify(self):
            raise AssertionError("duck-typed verification must not run")

    with pytest.raises(ReplayError) as fake:
        build_replay_plan(
            _DuckTypedVerifiedLab(),  # type: ignore[arg-type]
            replay_source.bundle,
            scenario="detector-demo",
            replay_window_start=REPLAY_START,
            policy=_policy(),
            environ={},
        )
    assert fake.value.code == "replay_artifact_unverified"

    unverified = TelcoLab(
        replay_source.provider,
        tmp_path / "not-fetched",
        downloader=_MemoryDownloader(replay_source.body),  # type: ignore[arg-type]
    )
    with pytest.raises(ReplayError) as missing:
        build_replay_plan(
            unverified,
            replay_source.bundle,
            scenario="detector-demo",
            replay_window_start=REPLAY_START,
            policy=_policy(),
            environ={},
        )
    assert missing.value.code == "replay_artifact_unverified"


def test_self_reported_bundle_is_rebuilt_from_locked_artifact_and_compared(
    replay_source: _ReplaySource,
) -> None:
    forged = _forge_observation_bundle(
        replay_source.bundle,
        value_delta=123.0,
    )
    assert (
        forged.manifest.source_artifact_sha256
        == replay_source.bundle.manifest.source_artifact_sha256
    )
    assert forged.manifest.adapter_id == replay_source.bundle.manifest.adapter_id
    assert (
        forged.manifest.source_license == replay_source.bundle.manifest.source_license
    )
    assert (
        forged.manifest.content_sha256 != replay_source.bundle.manifest.content_sha256
    )

    with pytest.raises(ReplayError) as caught:
        _plan(replay_source, bundle=forged)
    assert caught.value.code == "replay_bundle_unbound"


def test_verified_artifact_tampering_is_detected_before_re_adaptation(
    replay_source: _ReplaySource,
) -> None:
    replay_source.artifact_path.write_bytes(b"x" * len(replay_source.body))

    with pytest.raises(ReplayError) as caught:
        _plan(replay_source)
    assert caught.value.code == "replay_artifact_unverified"


def test_only_reviewed_metrics_units_and_quality_flags_can_cross_replay(
    replay_source: _ReplaySource,
) -> None:
    attacks = (
        _forge_observation_bundle(
            replay_source.bundle,
            metric_name="persistent_anomaly",
            unit="count",
        ),
        _forge_observation_bundle(replay_source.bundle, unit="percent"),
        _forge_observation_bundle(
            replay_source.bundle,
            quality_flags=("persistent_interference",),
        ),
    )
    for bundle in attacks:
        with pytest.raises(ReplayError) as caught:
            _plan(replay_source, bundle=bundle)
        assert caught.value.code == "replay_bundle_invalid"
        assert "persistent_interference" not in str(caught.value)


def test_adapter_emitted_missing_metric_flag_is_the_only_allowed_annotation(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "missing", missing_metric=True)
    plan = _plan(source)

    assert plan.events[0].quality_flags == ("MISSING_METRIC_VALUES",)
    assert "persistent_interference" not in str(plan.events[0].sink_payload())


@pytest.mark.parametrize(
    "environment",
    (
        {"RUNTIME_PROFILE": "cloud"},
        {"ACTION_MODE": "engineer_a2a"},
        {"ACTION_MODE": "simulate"},
        {"GOOGLE_CLOUD_PROJECT": "secret-project"},
        {"GOOGLE_PROJECT": "secret-project"},
        {"GOOGLE_APPLICATION_CREDENTIALS": "private-key.json"},
        {"SPANNER_EMULATOR_HOST": "127.0.0.1:9010"},
        {"ENGINEER_AGENT_URL": "http://127.0.0.1:9001"},
        {"NETWORK_OPERATOR_URL": "http://127.0.0.1:9002"},
        {"KUBECONFIG": "local-kubeconfig"},
        {"GITOPS_TARGET": "local-cluster"},
        {"RESOLVER_AGENT_URL": "http://127.0.0.1:9003"},
        {"NODE_ENV": "production"},
        {"PATH": "caller-supplied-unreviewed-environment"},
    ),
)
def test_environment_guard_fails_closed_without_reflecting_values(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ReplayError) as caught:
        validate_replay_environment(_policy(), environment)

    assert caught.value.code == "replay_environment_unsafe"
    assert "secret-project" not in str(caught.value)
    assert "private-key.json" not in str(caught.value)


@pytest.mark.parametrize(
    "process_environment",
    (
        {"ENGINEER_URL": "http://127.0.0.1:8081"},
        {"ENGINEER_ADDRESS": "http://127.0.0.1:8081"},
        {"NETWORK_AGENT_FILE": "networkagent.json"},
    ),
)
def test_default_process_environment_rejects_real_project_control_plane_keys(
    monkeypatch: pytest.MonkeyPatch,
    process_environment: dict[str, str],
) -> None:
    monkeypatch.setattr(replay_module.os, "environ", process_environment)

    with pytest.raises(ReplayError) as caught:
        validate_replay_environment(_policy())

    assert caught.value.code == "replay_environment_unsafe"
    assert "networkagent.json" not in str(caught.value)


def test_environment_guard_accepts_only_matching_safe_local_action_mode() -> None:
    validate_replay_environment(
        _policy(action_mode="simulate"),
        {
            "RUNTIME_PROFILE": "local",
            "NETWORKAGENT_PROFILE": "local",
            "TELCO_RUNTIME_PROFILE": "local",
            "ACTION_MODE": "simulate",
        },
    )
    validate_replay_environment(_policy(), {})


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://example.com/replay",
        "http://localhost.example/replay",
        "http://user:password@127.0.0.1:8080/replay",
        "http://127.0.0.1:8080/replay?target=cloud",
        "http://127.0.0.1:8080//example.com/replay",
        "file:///tmp/replay",
    ),
)
def test_policy_rejects_non_loopback_or_ambiguous_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        _policy(endpoint=endpoint)


def test_event_count_and_size_budgets_fail_before_replay(
    replay_source: _ReplaySource,
) -> None:
    cases = (
        (_policy(max_events=2), "replay_event_limit"),
        (_policy(max_payload_bytes=1), "replay_payload_limit"),
        (_policy(max_total_payload_bytes=1), "replay_payload_limit"),
    )
    for policy, expected in cases:
        with pytest.raises(ReplayError) as caught:
            _plan(replay_source, policy=policy)
        assert caught.value.code == expected

    with pytest.raises(ValidationError):
        _policy(max_resources=0)


def test_duration_budget_uses_rebuilt_artifact_window(tmp_path: Path) -> None:
    source = _source(
        tmp_path / "duration",
        offsets=(0, 61),
        labels=(False, False),
    )

    with pytest.raises(ReplayError) as caught:
        _plan(source, policy=_policy(max_duration_seconds=60))
    assert caught.value.code == "replay_duration_limit"


def test_replay_window_time_shift_overflow_has_a_fixed_error_code(
    replay_source: _ReplaySource,
) -> None:
    with pytest.raises(ReplayError) as caught:
        build_replay_plan(
            replay_source.lab,
            replay_source.bundle,
            scenario="detector-demo",
            replay_window_start=datetime.max.replace(tzinfo=UTC),
            policy=_policy(),
            environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": "disabled"},
        )

    assert caught.value.code == "replay_arguments_invalid"
    assert "9999" not in str(caught.value)


def test_models_reject_naive_time_unknown_label_and_changed_identity(
    replay_source: _ReplaySource,
) -> None:
    event = _plan(replay_source).events[0]
    payload = event.model_dump(mode="python")
    payload["replay_observed_at"] = payload["replay_observed_at"].replace(tzinfo=None)
    with pytest.raises(ValidationError):
        ReplayEvent.model_validate(payload)

    payload = event.model_dump(mode="python")
    payload["label"] = "persistent_interference"
    with pytest.raises(ValidationError):
        ReplayEvent.model_validate(payload)

    payload = event.model_dump(mode="python")
    first_name = next(iter(payload["metrics"]))
    payload["metrics"][first_name] = 99.0
    with pytest.raises(ValidationError):
        ReplayEvent.model_validate(payload)

    payload = event.model_dump(mode="python")
    payload["quality_flags"] = ("persistent_interference",)
    with pytest.raises(ValidationError):
        ReplayEvent.model_validate(payload)


def test_replay_api_is_exported_from_telco_lab() -> None:
    from telco_lab import (
        MAX_REPLAY_EVENTS,
        ReplayPlan as ExportedReplayPlan,
        build_replay_plan as exported_build_replay_plan,
    )

    assert MAX_REPLAY_EVENTS == 10_000
    assert ExportedReplayPlan is ReplayPlan
    assert exported_build_replay_plan is build_replay_plan
