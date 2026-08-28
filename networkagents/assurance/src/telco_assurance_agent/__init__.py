"""Deterministic, ADK-independent Local Assurance A2A service."""

from .app import create_app, initialize_assurance
from .card import ASSURANCE_AGENT_NAME, build_agent_card
from .config import AssuranceConfig
from .protocol import (
    AssuranceAnalysisRequest,
    AssuranceAnalyzeRequest,
    AssuranceCandidatePage,
    AssuranceConfirmationRequest,
    AssuranceConfirmationResult,
    AssuranceError,
    AssuranceScanRequest,
    parse_request_message,
)
from .service import AssuranceInterruption, AssuranceService
from .stores import (
    DuckDbPendingConfirmationStore,
    DuckDbTaskStore,
    initialize_assurance_database,
)

__all__ = [
    "ASSURANCE_AGENT_NAME",
    "AssuranceAnalysisRequest",
    "AssuranceAnalyzeRequest",
    "AssuranceCandidatePage",
    "AssuranceConfirmationRequest",
    "AssuranceConfirmationResult",
    "AssuranceError",
    "AssuranceScanRequest",
    "AssuranceConfig",
    "AssuranceInterruption",
    "AssuranceService",
    "DuckDbPendingConfirmationStore",
    "DuckDbTaskStore",
    "build_agent_card",
    "create_app",
    "initialize_assurance",
    "initialize_assurance_database",
    "parse_request_message",
]
