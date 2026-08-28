"""Composition-root tests for the dependency-free Local Profile."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace

import duckdb
import pytest

from telco_domain import IncidentRepository, MetricRepository, TelemetryRepository
from telco_local import (
    DeterministicRcaGateway,
    DuckDbIncidentRepository,
    DuckDbTelemetryRepository,
    JsonRuleRepository,
    LocalDetector,
    LocalProfile,
    MarkdownDocumentRepository,
)


def test_initialize_composes_one_shared_local_profile(local_config) -> None:
    documents_dir = local_config.database_path.parent / "documents"
    documents_dir.mkdir()
    (documents_dir / "lte.md").write_text("# LTE\nERAB 与 S1 安全配置。", encoding="utf-8")
    config = replace(local_config, documents_dir=documents_dir)

    profile = LocalProfile.initialize(config)

    assert profile.config is config
    assert profile.database_summary.performance_rows == 2
    assert profile.database_summary.trace_rows == 2
    assert isinstance(profile.incident_repository, DuckDbIncidentRepository)
    assert isinstance(profile.incident_repository, IncidentRepository)
    assert isinstance(profile.rule_repository, JsonRuleRepository)
    assert isinstance(profile.telemetry_repository, DuckDbTelemetryRepository)
    assert isinstance(profile.telemetry_repository, MetricRepository)
    assert isinstance(profile.telemetry_repository, TelemetryRepository)
    assert isinstance(profile.detector, LocalDetector)
    assert isinstance(profile.document_repository, MarkdownDocumentRepository)
    assert isinstance(profile.rca_gateway, DeterministicRcaGateway)


def test_initialize_without_documents_keeps_external_and_internal_search_off(
    local_config,
) -> None:
    profile = LocalProfile.initialize(local_config)
    assert profile.document_repository is None


def test_open_existing_composes_runtime_without_bootstrap(local_config) -> None:
    initialized = LocalProfile.initialize(local_config)
    opened = LocalProfile.open_existing(local_config)

    assert opened.database_summary == initialized.database_summary
    assert isinstance(opened.detector, LocalDetector)
    assert isinstance(opened.rca_gateway, DeterministicRcaGateway)


def test_open_existing_fails_closed_when_database_is_missing(local_config) -> None:
    with pytest.raises(FileNotFoundError):
        LocalProfile.open_existing(local_config)


def test_open_existing_never_repairs_or_applies_ddl(local_config) -> None:
    LocalProfile.initialize(local_config)
    with duckdb.connect(str(local_config.database_path)) as connection:
        connection.execute(
            "DROP INDEX canonical_incident_source_events_owner_idx"
        )

    with pytest.raises(RuntimeError, match="ownership index"):
        LocalProfile.open_existing(local_config)

    with duckdb.connect(
        str(local_config.database_path), read_only=True
    ) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM duckdb_indexes()
            WHERE index_name = 'canonical_incident_source_events_owner_idx'
            """
        ).fetchone() == (0,)


def test_open_existing_rejects_broken_table_shape_without_repair(
    local_config,
) -> None:
    LocalProfile.initialize(local_config)
    with duckdb.connect(str(local_config.database_path)) as connection:
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
    before = local_config.database_path.read_bytes()

    with pytest.raises(RuntimeError, match="table shape"):
        LocalProfile.open_existing(local_config)

    assert local_config.database_path.read_bytes() == before
    with duckdb.connect(
        str(local_config.database_path), read_only=True
    ) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info('canonical_incident_source_events')"
            ).fetchall()
        }
        assert "actor" not in columns


def test_open_existing_rejects_extra_unique_constraints(local_config) -> None:
    LocalProfile.initialize(local_config)
    with duckdb.connect(str(local_config.database_path)) as connection:
        connection.execute(
            "CREATE UNIQUE INDEX unexpected_status_owner "
            "ON canonical_incidents(status)"
        )

    with pytest.raises(RuntimeError, match="index set"):
        LocalProfile.open_existing(local_config)


def test_public_import_is_cloud_framework_and_credential_independent(tmp_path) -> None:
    forbidden = (
        "google.adk",
        "a2a",
        "google.cloud",
        "fastmcp",
        "langgraph",
        "litellm",
    )
    script = f"""
import json
import socket
import sys

def reject_network(*args, **kwargs):
    raise AssertionError("network access attempted during import")

socket.socket.connect = reject_network
import telco_local
forbidden = {forbidden!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
)
print(json.dumps({{"loaded": loaded, "schema": telco_local.LocalProfileConfig.__name__}}))
"""
    clean_environment = os.environ.copy()
    for name in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        clean_environment.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=clean_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"loaded": []' in completed.stdout
