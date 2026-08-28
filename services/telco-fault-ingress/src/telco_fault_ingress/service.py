"""Mode routing and exact acknowledgement semantics for fault ingestion."""

from __future__ import annotations

from collections.abc import Mapping
import logging

from pydantic import ValidationError
from telco_domain import (
    IdempotencyConflictError,
    SensitiveDataError,
    UnsafeIncidentWriteError,
)

from .config import FaultIngressConfig, FaultPipelineMode
from .models import (
    EventIngestRepository,
    IngressDecision,
    LegacyHandler,
    ParsedPubSubPush,
    PermanentIngressError,
)
from .normalizer import build_incident_trigger, normalize_fault_event


logger = logging.getLogger(__name__)


def _disposition_name(value: object) -> str:
    candidate = getattr(value, "value", value)
    return candidate.lower() if isinstance(candidate, str) else ""


def _result_matches_mode(
    result,
    envelope,
    mode: FaultPipelineMode,
    *,
    actor: str,
    idempotency_key: str,
) -> bool:
    if (
        result.source_event_id != envelope.source_event_id
        or result.trace_id != envelope.trace_id
    ):
        return False
    disposition = _disposition_name(result.disposition)
    original = _disposition_name(result.original_disposition)
    if disposition != "replayed":
        if mode is FaultPipelineMode.SHADOW and disposition != "shadow_recorded":
            return False
        if mode is FaultPipelineMode.CANONICAL and disposition not in {
            "created",
            "correlated",
        }:
            return False

    if original == "shadow_recorded":
        return (
            result.incident is None
            and result.source_association is None
            and result.outbox_event_id is None
        )
    if original not in {"created", "correlated"}:
        return False
    incident = result.incident
    association = result.source_association
    candidate = envelope.incident
    if incident is None or association is None or candidate is None:
        return False
    return (
        incident.correlation_key == candidate.correlation_key
        and incident.technology is candidate.technology
        and (original != "created" or incident.trace_id == envelope.trace_id)
        and association.incident_id == incident.incident_id
        and association.source_event_id == envelope.source_event_id
        and association.trace_id == envelope.trace_id
        and association.idempotency_key == idempotency_key
        and association.actor == actor
        and association.reason == "canonical source event ingestion"
    )


class FaultIngressService:
    """Route one validated push through exactly one configured owner."""

    def __init__(
        self,
        config: FaultIngressConfig,
        repository: EventIngestRepository,
        *,
        legacy_handler: LegacyHandler | None = None,
        clock=None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.legacy_handler = legacy_handler
        self._clock = clock

    async def process(self, push: ParsedPubSubPush) -> IngressDecision:
        mode = self.config.mode
        if mode is FaultPipelineMode.PAUSED:
            logger.warning("fault ingress paused code=FAULT_PIPELINE_PAUSED")
            return IngressDecision(503, "FAULT_PIPELINE_PAUSED")

        if mode is FaultPipelineMode.LEGACY:
            if self.legacy_handler is None:
                logger.error("legacy fault handler unavailable")
                return IngressDecision(503, "FAULT_LEGACY_UNAVAILABLE")
            try:
                outcome = await self.legacy_handler(push)
            except Exception:
                logger.error("legacy fault handler failed")
                return IngressDecision(503, "FAULT_LEGACY_FAILED")
            if outcome is False:
                return IngressDecision(503, "FAULT_LEGACY_FAILED")
            return IngressDecision(204, "FAULT_LEGACY_ACCEPTED")

        try:
            received_at = None if self._clock is None else self._clock()
            envelope = normalize_fault_event(
                push,
                received_at=received_at,
                max_event_age_seconds=self.config.max_event_age_seconds,
                max_future_skew_seconds=self.config.max_future_skew_seconds,
            )
            shadow = mode is FaultPipelineMode.SHADOW
            candidate_trigger = build_incident_trigger(envelope)
            trigger = None if shadow else candidate_trigger
            result = await self.repository.ingest(
                envelope,
                shadow=shadow,
                actor=self.config.actor,
                reason=(
                    "shadow source event observation"
                    if shadow
                    else "canonical source event ingestion"
                ),
                idempotency_key=(
                    None if trigger is None else trigger.idempotency_key
                ),
                outbox_payload=(
                    None if trigger is None else trigger.to_data_part()
                ),
            )
        except PermanentIngressError:
            raise
        except (
            IdempotencyConflictError,
            SensitiveDataError,
            UnsafeIncidentWriteError,
            ValidationError,
            ValueError,
        ):
            logger.warning("fault event rejected by canonical validation")
            raise PermanentIngressError("FAULT_CANONICAL_INVALID") from None
        except Exception:
            logger.error("canonical fault ingest dependency failed")
            return IngressDecision(503, "FAULT_DEPENDENCY_UNAVAILABLE")

        try:
            from telco_cloud import IngestResult

            validated_result = IngestResult.model_validate(result)
        except (ImportError, ValidationError, TypeError, ValueError):
            logger.error("canonical ingest returned an invalid result")
            return IngressDecision(503, "FAULT_INGEST_RESULT_INVALID")
        if not _result_matches_mode(
            validated_result,
            envelope,
            mode,
            actor=self.config.actor,
            idempotency_key=candidate_trigger.idempotency_key,
        ):
            logger.error("canonical ingest returned an invalid result binding")
            return IngressDecision(503, "FAULT_INGEST_RESULT_INVALID")
        disposition = validated_result.disposition.value
        logger.info("fault event durably accepted disposition=%s", disposition)
        return IngressDecision(
            204, f"FAULT_{disposition.upper()}", validated_result
        )


__all__ = ["FaultIngressService"]
