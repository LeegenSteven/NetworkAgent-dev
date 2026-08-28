"""Composition root for one explicitly configured Local Profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import duckdb

from .config import LocalProfileConfig
from .database import LOCAL_SCHEMA_VERSION, DatabaseSummary, initialize_database
from .detector import LocalDetector
from .documents import MarkdownDocumentRepository
from .incident_repository import DuckDbIncidentRepository
from .rca import DeterministicRcaGateway
from .rules import JsonRuleRepository
from .telemetry import DuckDbTelemetryRepository


Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class LocalProfile:
    """All Local Profile ports wired to one database and approved data roots."""

    config: LocalProfileConfig
    database_summary: DatabaseSummary
    incident_repository: DuckDbIncidentRepository
    rule_repository: JsonRuleRepository
    telemetry_repository: DuckDbTelemetryRepository
    detector: LocalDetector
    document_repository: MarkdownDocumentRepository | None
    rca_gateway: DeterministicRcaGateway

    @classmethod
    def initialize(
        cls,
        config: LocalProfileConfig,
        *,
        reset: bool = False,
        clock: Clock | None = None,
    ) -> LocalProfile:
        """Initialize storage and compose adapters without cloud/model clients."""

        summary = initialize_database(config, reset=reset)
        return cls._compose(config, summary, clock=clock)

    @classmethod
    def open_existing(
        cls,
        config: LocalProfileConfig,
        *,
        clock: Clock | None = None,
    ) -> LocalProfile:
        """Open an initialized Local Profile without DDL or source imports."""

        if not config.database_path.is_file():
            raise FileNotFoundError(config.database_path)
        if not config.rules_dir.is_dir():
            raise FileNotFoundError(config.rules_dir)
        if config.documents_dir is not None and not config.documents_dir.is_dir():
            raise FileNotFoundError(config.documents_dir)
        connection = duckdb.connect(str(config.database_path), read_only=True)
        try:
            schema_row = connection.execute(
                "SELECT value FROM local_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if schema_row is None or str(schema_row[0]) != LOCAL_SCHEMA_VERSION:
                raise RuntimeError("unsupported or missing Local Profile schema")
            performance_rows = int(
                connection.execute("SELECT COUNT(*) FROM performance").fetchone()[0]
            )
            trace_rows = int(
                connection.execute("SELECT COUNT(*) FROM cell_traces").fetchone()[0]
            )
            incident_rows = int(
                connection.execute(
                    "SELECT COUNT(*) FROM canonical_incidents"
                ).fetchone()[0]
            )
            connection.execute("SELECT * FROM performance_kpi LIMIT 0")
        except duckdb.Error:
            raise RuntimeError("Local Profile database is not initialized") from None
        finally:
            connection.close()
        summary = DatabaseSummary(
            database_path=config.database_path,
            schema_version=LOCAL_SCHEMA_VERSION,
            performance_rows=performance_rows,
            trace_rows=trace_rows,
            incident_rows=incident_rows,
        )
        return cls._compose(config, summary, clock=clock)

    @classmethod
    def _compose(
        cls,
        config: LocalProfileConfig,
        summary: DatabaseSummary,
        *,
        clock: Clock | None,
    ) -> LocalProfile:
        rules = JsonRuleRepository(config.rules_dir)
        incidents = DuckDbIncidentRepository(config, clock=clock)
        telemetry = DuckDbTelemetryRepository(config, clock=clock)
        documents = (
            MarkdownDocumentRepository(config.documents_dir)
            if config.documents_dir is not None
            else None
        )
        detector = LocalDetector(
            config,
            rule_repository=rules,
            incident_repository=incidents,
            clock=clock,
        )
        rca_arguments = {
            "document_repository": documents,
            "incident_repository": incidents,
        }
        if clock is not None:
            rca_arguments["clock"] = clock
        rca = DeterministicRcaGateway(
            rules,
            telemetry,
            **rca_arguments,
        )
        return cls(
            config=config,
            database_summary=summary,
            incident_repository=incidents,
            rule_repository=rules,
            telemetry_repository=telemetry,
            detector=detector,
            document_repository=documents,
            rca_gateway=rca,
        )


__all__ = ["LocalProfile"]
