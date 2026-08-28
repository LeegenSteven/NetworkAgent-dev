"""Strict, privacy-safe canonical fault ingress."""

from .app import create_app
from .boundary import parse_pubsub_push
from .config import FaultIngressConfig, FaultPipelineMode
from .normalizer import normalize_fault_event
from .service import FaultIngressService

__all__ = [
    "FaultIngressConfig",
    "FaultIngressService",
    "FaultPipelineMode",
    "create_app",
    "normalize_fault_event",
    "parse_pubsub_push",
]
