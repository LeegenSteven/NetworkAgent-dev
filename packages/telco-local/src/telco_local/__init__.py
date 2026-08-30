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
from .governance import (
    GovernanceAuthorizationError,
    GovernanceClockError,
    GovernanceIdempotencyConflictError,
    GovernanceNotFoundError,
    GovernanceResult,
    GovernanceStateError,
    LOCAL_SIMULATION_ACTION_TYPE,
    LocalApprovalGateway,
    LocalGovernanceEngine,
    LocalGovernanceError,
    LocalSimulationPolicy,
    LocalVerificationGateway,
    SimulatedActionGateway,
)
from .incident_repository import DuckDbIncidentRepository
from .lifecycle_projection import (
    LifecycleProjectionError,
    build_lifecycle_projection,
)
from .profile import LocalProfile
from .rca import DeterministicRcaGateway
from .rules import (
    BUBBLERAN_REPLAY_DETECTOR_ALGORITHM,
    BUBBLERAN_REPLAY_RULE_ID,
    JsonRuleRepository,
    RcaRule,
    rule_content_sha256,
)
from .telemetry import DuckDbTelemetryRepository


__all__ = [
    "BUBBLERAN_REPLAY_DETECTOR_ALGORITHM",
    "BUBBLERAN_REPLAY_RULE_ID",
    "DatabaseSummary",
    "DeterministicRcaGateway",
    "DeterministicKpiDetector",
    "DetectorCapacityError",
    "DuckDbIncidentRepository",
    "DuckDbTelemetryRepository",
    "GovernanceAuthorizationError",
    "GovernanceClockError",
    "GovernanceIdempotencyConflictError",
    "GovernanceNotFoundError",
    "GovernanceResult",
    "GovernanceStateError",
    "JsonRuleRepository",
    "LifecycleProjectionError",
    "LOCAL_SIMULATION_ACTION_TYPE",
    "LocalApprovalGateway",
    "LocalProfileConfig",
    "LocalGovernanceEngine",
    "LocalGovernanceError",
    "LocalSchemaMigrationRequiredError",
    "LocalDetector",
    "LocalProfile",
    "LocalSimulationPolicy",
    "LocalVerificationGateway",
    "MarkdownDocumentRepository",
    "RcaRule",
    "build_lifecycle_projection",
    "rule_content_sha256",
    "SimulatedActionGateway",
    "initialize_database",
]
