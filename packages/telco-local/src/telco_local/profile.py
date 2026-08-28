"""Composition root for one explicitly configured Local Profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .config import LocalProfileConfig
from .database import DatabaseSummary, initialize_database
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
