"""Deterministic, cloud-independent adapters for the Local Profile."""

from .config import LocalProfileConfig
from .database import (
    DatabaseSummary,
    LocalSchemaMigrationRequiredError,
    initialize_database,
)
from .detector import (
    DeterministicKpiDetector,
    DetectorCapacityError,
    LocalDetector,
)
from .documents import MarkdownDocumentRepository
from .incident_repository import DuckDbIncidentRepository
from .profile import LocalProfile
from .rca import DeterministicRcaGateway
from .rules import JsonRuleRepository, RcaRule
from .telemetry import DuckDbTelemetryRepository


__all__ = [
    "DatabaseSummary",
    "DeterministicRcaGateway",
    "DeterministicKpiDetector",
    "DetectorCapacityError",
    "DuckDbIncidentRepository",
    "DuckDbTelemetryRepository",
    "JsonRuleRepository",
    "LocalProfileConfig",
    "LocalSchemaMigrationRequiredError",
    "LocalDetector",
    "LocalProfile",
    "MarkdownDocumentRepository",
    "RcaRule",
    "initialize_database",
]
