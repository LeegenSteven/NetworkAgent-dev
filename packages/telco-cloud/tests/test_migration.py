from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import duckdb
import pytest
import telco_cloud.migration as migration_module

from telco_domain import (
    Incident,
    IncidentSnapshotImportResult,
    IncidentStatus,
    InMemoryIncidentRepository,
    SourceEventAssociation,
)
from telco_cloud.migration import (
    MAX_MIGRATION_BUNDLE_BYTES,
    MigrationBundleError,
    MigrationDependencyError,
    MigrationEntry,
    QuarantineCode,
    create_migration_bundle,
    dump_migration_bundle,
    export_migration_bundle,
    import_migration_bundle,
    load_migration_bundle,
)
from telco_cloud.migration_cli import _load, _write, main as migration_main


BASE = datetime(2040, 1, 1, tzinfo=UTC)


def _incident(incident_id: str, *sources: str, **updates) -> Incident:
    values = {
        "incident_id": incident_id,
        "trace_id": f"trace-{incident_id}",
        "correlation_key": f"correlation-{incident_id}",
        "source_event_ids": tuple(sources),
        "created_at": BASE,
        "updated_at": BASE,
        "detected_at": BASE,
    }
    values.update(updates)
    return Incident(**values)


def _association(incident_id: str, source_event_id: str) -> SourceEventAssociation:
    return SourceEventAssociation(
        incident_id=incident_id,
        source_event_id=source_event_id,
        registered_at=BASE,
        actor="local-detector",
        reason="canonical source evidence",
        idempotency_key=f"associate-{source_event_id}",
        trace_id=f"trace-{incident_id}",
    )


def _bundle(*entries: MigrationEntry):
    return create_migration_bundle(
        source_profile="local-lte-demo",
        exported_at=BASE,
        entries=entries,
    )


def test_source_profile_is_normalized_before_checksum() -> None:
    bundle = create_migration_bundle(
        source_profile="  local-lte-demo  ",
        exported_at=BASE,
        entries=(),
    )
    assert bundle.source_profile == "local-lte-demo"
    assert load_migration_bundle(dump_migration_bundle(bundle)) == bundle


def test_bundle_factory_rejects_unbounded_or_oversized_entries_without_consuming() -> None:
    entry = MigrationEntry(incident=_incident("bounded-factory"))

    class CountingIterable:
        def __init__(self) -> None:
            self.consumed = 0

        def __iter__(self):
            while True:
                self.consumed += 1
                yield entry

    unbounded = CountingIterable()
    with pytest.raises(MigrationBundleError, match="bounded sequence"):
        create_migration_bundle(
            source_profile="local-lte-demo",
            exported_at=BASE,
            entries=unbounded,
        )
    assert unbounded.consumed == 0

    with pytest.raises(MigrationBundleError, match="1000 incidents"):
        create_migration_bundle(
            source_profile="local-lte-demo",
            exported_at=BASE,
            entries=(entry,) * 1001,
        )


def test_export_round_trip_preserves_provenance_and_detects_tamper() -> None:
    async def scenario() -> None:
        source = InMemoryIncidentRepository(clock=lambda: BASE)
        first = _incident("incident-a", "event-a")
        await source.create_or_correlate(
            first,
            idempotency_key="create-a",
            actor="detector",
            reason="initial event",
            trace_id=first.trace_id,
        )
        correlated = _incident(
            "incident-correlated",
            "event-b",
            correlation_key=first.correlation_key,
        )
        await source.create_or_correlate(
            correlated,
            idempotency_key="correlate-b",
            actor="detector",
            reason="related event",
            trace_id=correlated.trace_id,
        )

        bundle = await export_migration_bundle(
            source,
            source_profile="local-lte-demo",
            exported_at=BASE,
        )
        assert len(bundle.entries) == 1
        assert tuple(
            item.source_event_id for item in bundle.entries[0].associations
        ) == ("event-a", "event-b")
        assert bundle.entries[0].incident.source_event_ids == ("event-a",)
        encoded = dump_migration_bundle(bundle)
        assert load_migration_bundle(encoded) == bundle

        changed = json.loads(encoded)
        changed["entries"][0]["incident"]["title"] = "changed after export"
        with pytest.raises(MigrationBundleError, match="checksum"):
            load_migration_bundle(json.dumps(changed).encode())

    asyncio.run(scenario())


def test_dry_run_is_zero_write_and_live_import_replays_exactly() -> None:
    async def scenario() -> None:
        incident = _incident("incident-import", "event-a")
        bundle = _bundle(
            MigrationEntry(
                incident=incident,
                associations=(
                    _association(incident.incident_id, "event-a"),
                    _association(incident.incident_id, "event-b"),
                ),
            )
        )
        target = InMemoryIncidentRepository(clock=lambda: BASE)

        preview = await import_migration_bundle(bundle, None, dry_run=True)
        assert preview.model_dump() == {
            "dry_run": True,
            "total": 1,
            "eligible": 1,
            "imported": 0,
            "replayed": 0,
            "quarantined": 0,
            "quarantine_counts": {},
            "quarantine_items": (),
        }
        assert await target.list() == ()

        imported = await import_migration_bundle(bundle, target, dry_run=False)
        assert (imported.imported, imported.replayed, imported.quarantined) == (
            1,
            0,
            0,
        )
        stored = await target.get(incident.incident_id)
        assert stored is not None
        assert stored.source_event_ids == ("event-a",)
        assert tuple(
            item.source_event_id
            for item in await target.source_event_associations(incident.incident_id)
        ) == ("event-a", "event-b")
        assert len(await target.history(incident.incident_id)) == 1

        replayed = await import_migration_bundle(bundle, target, dry_run=False)
        assert (replayed.imported, replayed.replayed, replayed.quarantined) == (
            0,
            1,
            0,
        )
        assert len(await target.list()) == 1
        assert len(await target.history(incident.incident_id)) == 1

    asyncio.run(scenario())


def test_ambiguous_legacy_and_advanced_lifecycle_are_quarantined() -> None:
    ambiguous_a = _incident("ambiguous-a", "shared-event")
    ambiguous_b = _incident("ambiguous-b", "shared-event")
    advanced_payload = _incident("advanced").model_dump(
        mode="python", round_trip=True
    )
    advanced_payload.update(status=IncidentStatus.TRIAGED, revision=1)
    advanced = Incident.model_validate(advanced_payload)
    legacy = _incident(
        "legacy",
        model_metadata={"legacy_source": "old-spanner-incident"},
    )
    eligible = _incident("eligible", "eligible-event")
    bundle = _bundle(
        MigrationEntry(
            incident=ambiguous_a,
            associations=(_association("ambiguous-a", "shared-event"),),
        ),
        MigrationEntry(
            incident=ambiguous_b,
            associations=(_association("ambiguous-b", "shared-event"),),
        ),
        MigrationEntry(incident=advanced),
        MigrationEntry(incident=legacy),
        MigrationEntry(
            incident=eligible,
            associations=(_association("eligible", "eligible-event"),),
        ),
    )

    async def scenario() -> None:
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        report = await import_migration_bundle(bundle, target, dry_run=False)
        assert report.total == 5
        assert report.eligible == 1
        assert report.imported == 1
        assert report.quarantined == 4
        assert report.quarantine_counts == {
            QuarantineCode.AMBIGUOUS_SOURCE_OWNERSHIP.value: 2,
            QuarantineCode.LEGACY_REQUIRES_MAPPING.value: 1,
            QuarantineCode.UNSUPPORTED_LIFECYCLE.value: 1,
        }
        assert tuple(
            (item.entry_index, item.incident_id, item.code)
            for item in report.quarantine_items
        ) == (
            (0, "ambiguous-a", QuarantineCode.AMBIGUOUS_SOURCE_OWNERSHIP),
            (1, "ambiguous-b", QuarantineCode.AMBIGUOUS_SOURCE_OWNERSHIP),
            (2, "advanced", QuarantineCode.UNSUPPORTED_LIFECYCLE),
            (3, "legacy", QuarantineCode.LEGACY_REQUIRES_MAPPING),
        )
        assert tuple(item.incident_id for item in await target.list()) == (
            "eligible",
        )

    asyncio.run(scenario())


def test_ambiguous_active_correlation_is_quarantined_without_ordered_winner() -> None:
    async def scenario() -> None:
        shared = "lte:shared-cell:availability"
        first = _incident("correlation-a", correlation_key=shared)
        second = _incident("correlation-b", correlation_key=shared)
        bundle = _bundle(
            MigrationEntry(incident=first),
            MigrationEntry(incident=second),
        )
        preview = await import_migration_bundle(bundle, None, dry_run=True)
        assert preview.eligible == 0
        assert preview.quarantine_counts == {
            QuarantineCode.AMBIGUOUS_CORRELATION_OWNERSHIP.value: 2,
        }

        target = InMemoryIncidentRepository(clock=lambda: BASE)
        live = await import_migration_bundle(bundle, target, dry_run=False)
        assert live.quarantine_items == preview.quarantine_items
        assert live.imported == 0
        assert await target.list() == ()

    asyncio.run(scenario())


def test_unsupported_active_incident_still_blocks_shared_correlation() -> None:
    advanced_payload = _incident(
        "advanced-owner",
        correlation_key="lte:shared-active-owner",
    ).model_dump(mode="python", round_trip=True)
    advanced_payload.update(status=IncidentStatus.TRIAGED, revision=1)
    advanced = Incident.model_validate(advanced_payload)
    candidate = _incident(
        "detected-owner",
        correlation_key="lte:shared-active-owner",
    )
    bundle = _bundle(
        MigrationEntry(incident=advanced),
        MigrationEntry(incident=candidate),
    )

    report = asyncio.run(import_migration_bundle(bundle, None, dry_run=True))

    assert report.eligible == 0
    assert report.quarantine_counts == {
        QuarantineCode.AMBIGUOUS_CORRELATION_OWNERSHIP.value: 2,
    }


def test_target_conflict_is_quarantined_without_mutating_existing() -> None:
    async def scenario() -> None:
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        existing = _incident("existing", "existing-event")
        await target.create(
            existing,
            idempotency_key="existing-create",
            actor="detector",
            reason="existing target incident",
            trace_id=existing.trace_id,
        )
        incoming = _incident(
            "incoming",
            "incoming-event",
            correlation_key=existing.correlation_key,
        )
        report = await import_migration_bundle(
            _bundle(
                MigrationEntry(
                    incident=incoming,
                    associations=(
                        _association("incoming", "incoming-event"),
                    ),
                )
            ),
            target,
            dry_run=False,
        )
        assert report.imported == 0
        assert report.eligible == 0
        assert report.quarantined == 1
        assert report.quarantine_counts == {
            QuarantineCode.TARGET_CONFLICT.value: 1,
        }
        assert tuple(item.incident_id for item in await target.list()) == (
            existing.incident_id,
        )

    asyncio.run(scenario())


def test_dependency_failure_is_retryable_not_quarantined() -> None:
    class BrokenRepository:
        async def import_detected_snapshot(self, *args, **kwargs):
            raise OSError("private dependency detail")

    with pytest.raises(MigrationDependencyError, match="dependency failed"):
        asyncio.run(
            import_migration_bundle(
                _bundle(MigrationEntry(incident=_incident("dependency"))),
                BrokenRepository(),
                dry_run=False,
            )
        )


@pytest.mark.parametrize("invalid_replayed", (False, 1))
def test_import_rejects_drifted_or_invalid_target_outcome(
    invalid_replayed,
) -> None:
    incident = _incident("drifted-target")
    bundle = _bundle(MigrationEntry(incident=incident))

    class DriftedRepository:
        async def import_detected_snapshot(self, *args, **kwargs):
            del args, kwargs
            return IncidentSnapshotImportResult(
                incident=incident.model_copy(update={"title": "drifted"}),
                replayed=invalid_replayed,
            )

    with pytest.raises(MigrationBundleError, match="outcome|snapshot"):
        asyncio.run(
            import_migration_bundle(
                bundle,
                DriftedRepository(),
                dry_run=False,
            )
        )


def test_partial_dependency_failure_replays_committed_prefix() -> None:
    class FailSecondOnce:
        def __init__(self, target) -> None:
            self.target = target
            self.failed = False

        async def import_detected_snapshot(self, incident, *args, **kwargs):
            if incident.incident_id == "partial-b" and not self.failed:
                self.failed = True
                raise OSError("temporary target outage")
            return await self.target.import_detected_snapshot(
                incident, *args, **kwargs
            )

    async def scenario() -> None:
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        flaky = FailSecondOnce(target)
        bundle = _bundle(
            MigrationEntry(incident=_incident("partial-a")),
            MigrationEntry(incident=_incident("partial-b")),
        )

        with pytest.raises(MigrationDependencyError, match="dependency failed"):
            await import_migration_bundle(bundle, flaky, dry_run=False)
        assert tuple(item.incident_id for item in await target.list()) == (
            "partial-a",
        )

        recovered = await import_migration_bundle(bundle, flaky, dry_run=False)
        assert (recovered.imported, recovered.replayed) == (1, 1)
        assert tuple(item.incident_id for item in await target.list()) == (
            "partial-a",
            "partial-b",
        )
        assert len(await target.history("partial-a")) == 1
        assert len(await target.history("partial-b")) == 1

    asyncio.run(scenario())


def test_concurrent_exact_import_reports_one_commit_and_replays() -> None:
    async def scenario() -> None:
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        bundle = _bundle(MigrationEntry(incident=_incident("concurrent-import")))
        reports = await asyncio.gather(
            *(
                import_migration_bundle(bundle, target, dry_run=False)
                for _ in range(50)
            )
        )
        assert sum(report.imported for report in reports) == 1
        assert sum(report.replayed for report in reports) == 49
        assert len(await target.list()) == 1
        assert len(await target.history("concurrent-import")) == 1

    asyncio.run(scenario())


def test_selectorless_snapshot_exact_replay_is_supported() -> None:
    async def scenario() -> None:
        incident = _incident("selectorless", correlation_key=None)
        bundle = _bundle(MigrationEntry(incident=incident))
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        first = await import_migration_bundle(bundle, target, dry_run=False)
        second = await import_migration_bundle(bundle, target, dry_run=False)
        assert (first.imported, first.replayed) == (1, 0)
        assert (second.imported, second.replayed) == (0, 1)
        assert len(await target.history(incident.incident_id)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "data",
    (
        b'{"schema_version":"1.0","schema_version":"1.0"}',
        b'{"value":NaN}',
        b'{"value":1e9999}',
        b'{"value":"\\ud800"}',
    ),
)
def test_loader_rejects_noncanonical_json_without_reflection(data: bytes) -> None:
    with pytest.raises(MigrationBundleError) as error:
        load_migration_bundle(data)
    assert "ud800" not in str(error.value).lower()


@pytest.mark.parametrize(
    "replacement",
    (
        b'"SAFE_TOKEN","probe":"SAFE_TOKEN"',
        b"NaN",
        b"1e9999",
        b'"\\ud800"',
    ),
)
def test_loader_rejects_noncanonical_injection_into_valid_bundle_before_checksum(
    replacement: bytes,
    monkeypatch,
) -> None:
    incident = _incident(
        "valid-wire-baseline",
        model_metadata={"probe": "SAFE_TOKEN"},
    )
    encoded = dump_migration_bundle(
        _bundle(MigrationEntry(incident=incident))
    )
    assert encoded.count(b'"SAFE_TOKEN"') == 1
    injected = encoded.replace(b'"SAFE_TOKEN"', replacement, 1)
    checksum_called = False

    def forbidden_checksum(payload):
        nonlocal checksum_called
        del payload
        checksum_called = True
        return "0" * 64

    monkeypatch.setattr(migration_module, "_checksum", forbidden_checksum)
    with pytest.raises(MigrationBundleError):
        load_migration_bundle(injected)
    assert checksum_called is False


def test_bundle_factory_enforces_privacy_and_exact_canonical_depth() -> None:
    with pytest.raises(MigrationBundleError, match="unsafe|invalid"):
        _bundle(
            MigrationEntry(
                incident=_incident(
                    "unsafe-entry",
                    model_metadata={"imsi": "208930000000001"},
                )
            )
        )

    def nested(levels: int):
        value: object = "leaf"
        for _ in range(levels):
            value = {"safe": value}
        return value

    at_limit = _incident(
        "depth-at-limit",
        model_metadata={"nested": nested(21)},
    )
    over_limit = _incident(
        "depth-over-limit",
        model_metadata={"nested": nested(22)},
    )
    assert migration_module._json_depth(
        at_limit.model_dump(mode="json", round_trip=True)
    ) == 24
    assert migration_module._json_depth(
        over_limit.model_dump(mode="json", round_trip=True)
    ) == 25
    assert _bundle(MigrationEntry(incident=at_limit)).entries[0].incident == at_limit
    with pytest.raises(MigrationBundleError, match="canonical depth"):
        _bundle(MigrationEntry(incident=over_limit))


def test_loader_rejects_raw_size_budget() -> None:
    with pytest.raises(MigrationBundleError, match="16 MiB"):
        load_migration_bundle(b"x" * (MAX_MIGRATION_BUNDLE_BYTES + 1))


def test_per_incident_budget_is_identical_for_preview_and_live() -> None:
    oversized = _incident(
        "oversized",
        model_metadata={"safe_blob": "x" * 300_000},
    )
    with pytest.raises(MigrationBundleError, match="canonical size"):
        _bundle(MigrationEntry(incident=oversized))


def test_missing_provenance_is_actionable_and_never_written() -> None:
    async def scenario() -> None:
        incident = _incident("missing-source", "source-a")
        bundle = _bundle(MigrationEntry(incident=incident))
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        report = await import_migration_bundle(bundle, target, dry_run=False)
        assert report.eligible == 0
        assert report.imported == 0
        assert report.quarantine_items[0].entry_index == 0
        assert report.quarantine_items[0].incident_id == incident.incident_id
        assert report.quarantine_items[0].code is QuarantineCode.MISSING_SOURCE_PROVENANCE
        assert await target.list() == ()

    asyncio.run(scenario())


def test_export_rejects_more_than_1000_associations_without_truncation() -> None:
    incident = _incident("association-overflow")
    associations = tuple(
        _association(incident.incident_id, f"source-{index:04d}")
        for index in range(1001)
    )

    class OversizedAssociationRepository:
        async def list(self, *, limit, offset, **kwargs):
            del kwargs
            return (incident,) if offset == 0 and limit >= 1 else ()

        async def source_event_associations(
            self,
            incident_id,
            *,
            limit,
            offset,
        ):
            assert incident_id == incident.incident_id
            return associations[offset : offset + limit]

    with pytest.raises(MigrationBundleError, match="1000 source associations"):
        asyncio.run(
            export_migration_bundle(
                OversizedAssociationRepository(),
                source_profile="local-lte-demo",
                exported_at=BASE,
            )
        )


def test_export_enforces_bundle_budget_before_materializing_all_incidents() -> None:
    class OversizedRepository:
        def __init__(self) -> None:
            self.list_calls = 0

        async def list(self, *, limit, offset, **kwargs):
            del kwargs
            self.list_calls += 1
            if offset >= 1000:
                return ()
            return (
                _incident(
                    f"large-{offset:04d}",
                    model_metadata={"safe_blob": "x" * 200_000},
                ),
            )

        async def source_event_associations(self, *args, **kwargs):
            return ()

    repository = OversizedRepository()
    with pytest.raises(MigrationBundleError, match="16 MiB"):
        asyncio.run(
            export_migration_bundle(
                repository,
                source_profile="local-lte-demo",
                exported_at=BASE,
            )
        )
    assert repository.list_calls < 100


def test_live_import_preserves_every_source_association_field() -> None:
    async def scenario() -> None:
        incident = _incident("provenance", "source-a")
        associations = (
            _association(incident.incident_id, "source-a"),
            SourceEventAssociation(
                incident_id=incident.incident_id,
                source_event_id="source-b",
                registered_at=BASE,
                actor="fault-ingress",
                reason="related source event",
                idempotency_key="associate-source-b",
                trace_id="trace-related-event",
            ),
        )
        target = InMemoryIncidentRepository(clock=lambda: BASE)
        report = await import_migration_bundle(
            _bundle(MigrationEntry(incident=incident, associations=associations)),
            target,
            dry_run=False,
        )
        assert report.imported == 1
        assert (await target.get(incident.incident_id)).source_event_ids == (
            "source-a",
        )
        assert await target.source_event_associations(incident.incident_id) == associations

    asyncio.run(scenario())


def test_cli_export_validate_dry_run_and_live_import(tmp_path) -> None:
    async def prepare():
        source = InMemoryIncidentRepository(clock=lambda: BASE)
        incident = _incident("cli-incident", "cli-event")
        await source.create(
            incident,
            idempotency_key="cli-create",
            actor="detector",
            reason="CLI export fixture",
            trace_id=incident.trace_id,
        )
        return source

    source = asyncio.run(prepare())
    target = InMemoryIncidentRepository(clock=lambda: BASE)
    bundle_path = tmp_path / "migration.json"
    stdout = StringIO()
    stderr = StringIO()
    assert migration_main(
        [
            "export-duckdb",
            "--database",
            str(tmp_path / "unused.duckdb"),
            "--output",
            str(bundle_path),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=stdout,
        stderr=stderr,
        source_repository=source,
        clock=lambda: BASE,
    ) == 0
    assert json.loads(stdout.getvalue())["status"] == "EXPORTED"
    assert stderr.getvalue() == ""

    stdout = StringIO()
    assert migration_main(
        ["import-spanner", "--input", str(bundle_path), "--offline-plan"],
        environ={},
        stdout=stdout,
        target_repository=None,
    ) == 0
    assert json.loads(stdout.getvalue())["status"] == "OFFLINE_PLAN"
    assert asyncio.run(target.list()) == ()

    stdout = StringIO()
    assert migration_main(
        ["import-spanner", "--input", str(bundle_path)],
        environ={},
        stdout=stdout,
        target_repository=target,
    ) == 0
    assert json.loads(stdout.getvalue())["status"] == "IMPORTED"
    assert len(asyncio.run(target.list())) == 1


def test_cli_help_is_inert(capsys) -> None:
    with pytest.raises(SystemExit) as result:
        migration_main(["--help"], environ={})
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "export-duckdb" in output
    assert "import-spanner" in output


def test_cli_live_import_rejects_non_migration_database_role(tmp_path) -> None:
    bundle_path = tmp_path / "migration.json"
    bundle_path.write_bytes(dump_migration_bundle(_bundle()))
    stderr = StringIO()
    result = migration_main(
        ["import-spanner", "--input", str(bundle_path)],
        environ={
            "GOOGLE_PROJECT": "p3test1",
            "GOOGLE_SPANNER_INSTANCE": "p3instance",
            "GOOGLE_SPANNER_DATABASE": "p3database",
            "TELCO_SPANNER_DATABASE_ROLE": "telco_mcp_reader",
        },
        stdout=StringIO(),
        stderr=stderr,
    )
    assert result == 2
    assert json.loads(stderr.getvalue()) == {"error": "MIGRATION_INVALID"}


def test_cli_missing_duckdb_source_returns_fixed_error_without_path(tmp_path) -> None:
    missing = tmp_path / "private-source-name.duckdb"
    stderr = StringIO()
    result = migration_main(
        [
            "export-duckdb",
            "--database",
            str(missing),
            "--output",
            str(tmp_path / "unused.json"),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=StringIO(),
        stderr=stderr,
    )
    assert result == 2
    assert json.loads(stderr.getvalue()) == {"error": "MIGRATION_INVALID"}
    assert str(missing) not in stderr.getvalue()


def test_cli_missing_optional_local_dependency_returns_fixed_error(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "telco_local", None)
    stderr = StringIO()
    result = migration_main(
        [
            "export-duckdb",
            "--database",
            str(tmp_path / "source.duckdb"),
            "--output",
            str(tmp_path / "snapshot.json"),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=StringIO(),
        stderr=stderr,
    )
    assert result == 4
    assert json.loads(stderr.getvalue()) == {
        "error": "MIGRATION_DEPENDENCY_UNAVAILABLE"
    }


def test_cli_source_failure_returns_fixed_error_without_detail(tmp_path) -> None:
    private_detail = "private-source-location"

    class BrokenSource:
        async def list(self, **kwargs):
            del kwargs
            raise OSError(private_detail)

    stderr = StringIO()
    result = migration_main(
        [
            "export-duckdb",
            "--database",
            str(tmp_path / "unused.duckdb"),
            "--output",
            str(tmp_path / "snapshot.json"),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=StringIO(),
        stderr=stderr,
        source_repository=BrokenSource(),
        clock=lambda: BASE,
    )
    assert result == 4
    assert json.loads(stderr.getvalue()) == {
        "error": "MIGRATION_DEPENDENCY_UNAVAILABLE"
    }
    assert private_detail not in stderr.getvalue()


def test_cli_rejects_broken_duckdb_shape_without_modifying_source(tmp_path) -> None:
    from telco_local import LocalProfileConfig, initialize_database

    workspace = Path(__file__).resolve().parents[3]
    config = LocalProfileConfig(
        database_path=tmp_path / "source.duckdb",
        performance_csv_path=(
            workspace / "data" / "samples" / "lte-demo" / "performance.csv"
        ),
        safe_trace_csv_path=(
            workspace
            / "data"
            / "samples"
            / "lte-demo"
            / "safe-cell-traces.csv"
        ),
        rules_dir=workspace / "data" / "rca-rules" / "lte",
        source_timezone="UTC",
    )
    initialize_database(config)
    with duckdb.connect(str(config.database_path)) as connection:
        connection.execute(
            "DROP INDEX canonical_incident_source_events_source_idx"
        )
        connection.execute(
            "DROP INDEX canonical_incident_source_events_owner_idx"
        )
        connection.execute(
            "ALTER TABLE canonical_incident_source_events DROP COLUMN actor"
        )
        connection.execute(
            "CREATE INDEX canonical_incident_source_events_source_idx "
            "ON canonical_incident_source_events(source_event_id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX canonical_incident_source_events_owner_idx "
            "ON canonical_incident_source_events(source_event_id)"
        )
    before = hashlib.sha256(config.database_path.read_bytes()).digest()
    stderr = StringIO()

    result = migration_main(
        [
            "export-duckdb",
            "--database",
            str(config.database_path),
            "--output",
            str(tmp_path / "snapshot.json"),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert result == 2
    assert json.loads(stderr.getvalue()) == {"error": "MIGRATION_INVALID"}
    assert hashlib.sha256(config.database_path.read_bytes()).digest() == before
    assert not (tmp_path / "snapshot.json").exists()


def test_cli_rejects_oversized_file_before_reading_it(tmp_path) -> None:
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.seek(MAX_MIGRATION_BUNDLE_BYTES)
        stream.write(b"x")
    with pytest.raises(MigrationBundleError, match="16 MiB"):
        _load(path)


def test_cli_atomic_output_leaves_no_partial_destination(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "bundle.json"

    def fail_link(*args, **kwargs):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr("telco_cloud.migration_cli.os.link", fail_link)
    with pytest.raises(MigrationBundleError, match="could not be written"):
        _write(destination, b"verified snapshot", overwrite=False)
    assert not destination.exists()
    assert tuple(tmp_path.glob(".bundle.json.*.tmp")) == ()


def test_cli_duckdb_export_is_read_only(tmp_path) -> None:
    from telco_local import LocalProfileConfig, initialize_database

    workspace = Path(__file__).resolve().parents[3]
    config = LocalProfileConfig(
        database_path=tmp_path / "source.duckdb",
        performance_csv_path=(
            workspace / "data" / "samples" / "lte-demo" / "performance.csv"
        ),
        safe_trace_csv_path=(
            workspace
            / "data"
            / "samples"
            / "lte-demo"
            / "safe-cell-traces.csv"
        ),
        rules_dir=workspace / "data" / "rca-rules" / "lte",
        source_timezone="UTC",
    )
    initialize_database(config)
    before = hashlib.sha256(config.database_path.read_bytes()).digest()
    output_path = tmp_path / "snapshot.json"

    result = migration_main(
        [
            "export-duckdb",
            "--database",
            str(config.database_path),
            "--output",
            str(output_path),
            "--source-profile",
            "local-lte-demo",
        ],
        stdout=StringIO(),
        stderr=StringIO(),
        clock=lambda: BASE,
    )

    assert result == 0
    assert hashlib.sha256(config.database_path.read_bytes()).digest() == before
    assert load_migration_bundle(output_path.read_bytes()).entries == ()
