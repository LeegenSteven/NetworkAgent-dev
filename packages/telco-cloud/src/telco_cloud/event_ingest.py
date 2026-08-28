"""Durable, idempotent source-event inbox and Incident outbox adapter."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from telco_domain.contracts import IncidentTrigger
from telco_domain.models import Incident, JsonValue, SourceEventAssociation

from ._common import (
    Clock,
    assert_safe,
    fingerprint,
    json_safe,
    require_non_empty,
    require_write_metadata,
    utc_now,
)
from ._spanner import json_object, read_one
from .incident_repository import SpannerIncidentRepository, _scope
from .outbox_repository import SpannerOutboxRepository


_Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
_NonEmpty = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096)
]
_Sha256 = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event timestamps must be timezone-aware")
    return value.astimezone(UTC)


class SourceEventEnvelope(BaseModel):
    """Privacy-safe normalized event; raw provider payload never crosses here."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
        validate_default=True,
    )

    schema_version: Literal["1.0"] = "1.0"
    source_event_id: _Identifier
    source: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_048)
    ]
    event_type: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    occurred_at: datetime
    received_at: datetime
    payload_sha256: _Sha256
    trace_id: _Identifier
    incident: Incident | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_envelope(self) -> "SourceEventEnvelope":
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        object.__setattr__(self, "received_at", _utc(self.received_at))
        if self.occurred_at > self.received_at + timedelta(minutes=5):
            raise ValueError("occurred_at must not be over 300 seconds in the future")
        if self.incident is not None and self.incident.trace_id != self.trace_id:
            raise ValueError("event trace_id must match incident.trace_id")
        assert_safe(self, boundary="source-event-envelope")
        return self


class IngestDisposition(StrEnum):
    CREATED = "created"
    CORRELATED = "correlated"
    REPLAYED = "replayed"
    SHADOW_RECORDED = "shadow_recorded"


class IngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    disposition: IngestDisposition
    original_disposition: IngestDisposition | None = None
    source_event_id: _Identifier
    trace_id: _Identifier
    incident: Incident | None = None
    source_association: SourceEventAssociation | None = None
    outbox_event_id: _Identifier | None = None

    @model_validator(mode="after")
    def validate_disposition_shape(self) -> "IngestResult":
        original = self.original_disposition
        if self.disposition is IngestDisposition.REPLAYED:
            if original in {None, IngestDisposition.REPLAYED}:
                raise ValueError("REPLAYED requires an original disposition")
        else:
            if original is None:
                original = self.disposition
                object.__setattr__(self, "original_disposition", original)
            elif original is not self.disposition:
                raise ValueError(
                    "non-replay result original_disposition must match disposition"
                )
        if self.outbox_event_id is not None and self.incident is None:
            raise ValueError("an outbox event requires an incident")
        if self.source_association is not None:
            if self.incident is None:
                raise ValueError("source association requires an incident")
            if (
                self.source_association.incident_id != self.incident.incident_id
                or self.source_association.source_event_id != self.source_event_id
                or self.source_association.trace_id != self.trace_id
            ):
                raise ValueError("source association binding mismatch")

        shape = original
        if shape is IngestDisposition.CREATED:
            if (
                self.incident is None
                or self.source_association is None
                or self.outbox_event_id is None
            ):
                raise ValueError(
                    "CREATED requires incident, source association, and outbox_event_id"
                )
        elif shape is IngestDisposition.CORRELATED:
            if (
                self.incident is None
                or self.source_association is None
                or self.outbox_event_id is not None
            ):
                raise ValueError(
                    "CORRELATED requires incident and source association without a new outbox"
                )
        elif shape is IngestDisposition.SHADOW_RECORDED:
            if (
                self.incident is not None
                or self.source_association is not None
                or self.outbox_event_id is not None
            ):
                raise ValueError(
                    "SHADOW_RECORDED cannot carry incident, association, or outbox"
                )
        return self


_INBOX_COLUMNS = (
    "source_event_id",
    "source",
    "event_type",
    "payload_sha256",
    "envelope_fingerprint",
    "trace_id",
    "received_at",
    "processed_at",
    "disposition",
    "incident_id",
    "outbox_event_id",
    "result_payload",
)
_OUTBOX_COLUMNS = (
    "event_id",
    "incident_id",
    "source_event_id",
    "event_type",
    "payload",
    "status",
    "attempts",
    "available_at",
    "created_at",
    "published_at",
    "lease_owner",
    "lease_expires_at",
    "last_error_code",
)


class SpannerEventIngestRepository:
    """Commit inbox, canonical Incident, audit, idempotency, and outbox atomically."""

    def __init__(self, database: Any, clock: Clock | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._incidents = SpannerIncidentRepository(database, clock=self._clock)

    def _now(self) -> datetime:
        return utc_now(self._clock)

    async def ingest(
        self,
        envelope: SourceEventEnvelope,
        *,
        shadow: bool = False,
        actor: str = "fault-ingress",
        reason: str = "source event ingestion",
        idempotency_key: str | None = None,
        outbox_payload: Mapping[str, object] | None = None,
    ) -> IngestResult:
        normalized = SourceEventEnvelope.model_validate(envelope)
        effective_key = idempotency_key or self._event_idempotency_key(
            normalized.source_event_id
        )
        require_write_metadata(
            idempotency_key=effective_key,
            actor=actor,
            reason=reason,
            trace_id=normalized.trace_id,
        )
        if not shadow:
            if normalized.incident is None:
                raise ValueError("non-shadow ingestion requires a canonical incident")
            if normalized.source_event_id not in normalized.incident.source_event_ids:
                raise ValueError(
                    "incident.source_event_ids must include the source event identity"
                )
        if shadow and outbox_payload is not None:
            raise ValueError("shadow ingestion cannot accept an outbox payload")
        trigger_template: IncidentTrigger | None = None
        if outbox_payload is not None:
            assert_safe(outbox_payload, boundary="outbox-payload")
            trigger_template = IncidentTrigger.model_validate(dict(outbox_payload))
            assert normalized.incident is not None
            self._validate_trigger_template(
                trigger_template,
                envelope=normalized,
                incident=normalized.incident,
                idempotency_key=effective_key,
            )
        envelope_fingerprint = fingerprint(
            "source_event",
            {
                "source_event_id": normalized.source_event_id,
                "source": normalized.source,
                "event_type": normalized.event_type,
                "occurred_at": normalized.occurred_at,
                "payload_sha256": normalized.payload_sha256,
                "trace_id": normalized.trace_id,
                "incident": normalized.incident,
                "attributes": normalized.attributes,
            },
        )
        processed_at = self._now()

        def callback(transaction):
            replay_row = read_one(
                transaction,
                "CanonicalSourceEventInboxV2",
                (
                    "source_event_id",
                    "source",
                    "event_type",
                    "payload_sha256",
                    "envelope_fingerprint",
                    "trace_id",
                    "received_at",
                    "processed_at",
                    "disposition",
                    "incident_id",
                    "outbox_event_id",
                    "result_payload",
                ),
                (normalized.source_event_id,),
            )
            if replay_row is not None:
                if (
                    replay_row[0] != normalized.source_event_id
                    or replay_row[1] != normalized.source
                    or replay_row[2] != normalized.event_type
                    or replay_row[3] != normalized.payload_sha256
                    or replay_row[4] != envelope_fingerprint
                    or replay_row[5] != normalized.trace_id
                ):
                    raise ValueError(
                        "source event identity was replayed with a different payload"
                    )
                if (
                    not isinstance(replay_row[6], datetime)
                    or replay_row[6].tzinfo is None
                    or replay_row[6].utcoffset() is None
                    or not isinstance(replay_row[7], datetime)
                    or replay_row[7].tzinfo is None
                    or replay_row[7].utcoffset() is None
                ):
                    raise RuntimeError(
                        "persisted source-event timestamps are invalid"
                    )
                original = IngestResult.model_validate(replay_row[11])
                result_incident_id = (
                    None
                    if original.incident is None
                    else original.incident.incident_id
                )
                if (
                    original.source_event_id != normalized.source_event_id
                    or original.trace_id != normalized.trace_id
                    or original.disposition.value != replay_row[8]
                    or result_incident_id != replay_row[9]
                    or original.outbox_event_id != replay_row[10]
                ):
                    raise RuntimeError("persisted source-event Inbox binding mismatch")
                persisted_incident: Incident | None = None
                if original.incident is not None:
                    persisted_incident = self._incidents._get_tx(
                        transaction, original.incident.incident_id
                    )
                    if (
                        persisted_incident is None
                        or self._incident_identity(persisted_incident)
                        != self._incident_identity(original.incident)
                    ):
                        raise RuntimeError(
                            "persisted source-event Incident result mismatch"
                        )
                if original.source_association is not None:
                    persisted_association = (
                        self._incidents._get_source_association_tx(
                            transaction,
                            original.source_association.incident_id,
                            normalized.source_event_id,
                        )
                    )
                    if persisted_association != original.source_association:
                        raise RuntimeError(
                            "persisted source-event association result mismatch"
                        )
                    if normalized.incident is None:
                        raise RuntimeError(
                            "persisted canonical result is missing its replay candidate"
                        )
                    replay_scope = _scope(
                        "create_or_correlate",
                        normalized.incident.incident_id,
                        original.source_association.idempotency_key,
                    )
                    replay_fingerprint = fingerprint(
                        "create_or_correlate",
                        {
                            "incident": normalized.incident,
                            "actor": original.source_association.actor,
                            "reason": original.source_association.reason,
                            "trace_id": original.source_association.trace_id,
                        },
                    )
                    persisted_result = self._incidents._replay(
                        transaction, replay_scope, replay_fingerprint
                    )
                    if persisted_result != original.incident:
                        raise RuntimeError(
                            "persisted source-event idempotency result mismatch"
                        )
                if original.original_disposition is IngestDisposition.CREATED:
                    if original.outbox_event_id is None:
                        raise RuntimeError(
                            "persisted CREATED result is missing its outbox identity"
                        )
                    outbox = SpannerOutboxRepository._get_tx(
                        transaction, original.outbox_event_id
                    )
                    association = original.source_association
                    if (
                        outbox is None
                        or original.incident is None
                        or association is None
                        or outbox.incident_id != original.incident.incident_id
                        or outbox.source_event_id != normalized.source_event_id
                        or outbox.payload.incident != original.incident
                        or outbox.payload.trace_id != original.trace_id
                        or outbox.payload.idempotency_key
                        != association.idempotency_key
                        or outbox.payload.sent_at != replay_row[6]
                    ):
                        raise RuntimeError(
                            "persisted source-event outbox result mismatch"
                        )
                return IngestResult.model_validate(
                    {
                        **original.model_dump(mode="python", round_trip=True),
                        "disposition": IngestDisposition.REPLAYED,
                    }
                )

            if shadow:
                result = IngestResult(
                    disposition=IngestDisposition.SHADOW_RECORDED,
                    source_event_id=normalized.source_event_id,
                    trace_id=normalized.trace_id,
                )
                self._insert_inbox_tx(
                    transaction,
                    normalized,
                    envelope_fingerprint=envelope_fingerprint,
                    result=result,
                    processed_at=processed_at,
                )
                return result

            assert normalized.incident is not None
            request_scope = _scope(
                "create_or_correlate",
                normalized.incident.incident_id,
                effective_key,
            )
            request_fingerprint = fingerprint(
                "create_or_correlate",
                {
                    "incident": normalized.incident,
                    "actor": actor,
                    "reason": reason,
                    "trace_id": normalized.trace_id,
                },
            )
            incident, created, associations = self._incidents._create_or_correlate_tx(
                transaction,
                normalized.incident,
                scope=request_scope,
                request_fingerprint=request_fingerprint,
                actor=actor,
                reason=reason,
                trace_id=normalized.trace_id,
                trusted_now=processed_at,
            )
            disposition = (
                IngestDisposition.CREATED if created else IngestDisposition.CORRELATED
            )
            source_association = next(
                (
                    item
                    for item in associations
                    if item.source_event_id == normalized.source_event_id
                ),
                None,
            )
            if source_association is None:
                raise RuntimeError("source event association was not persisted")
            outbox_event_id: str | None = None
            if created:
                outbox_event_id = self._outbox_event_id(
                    normalized.source_event_id, incident.incident_id
                )
                trigger = self._materialize_trigger(
                    trigger_template,
                    envelope=normalized,
                    incident=incident,
                    idempotency_key=effective_key,
                )
                payload = trigger.to_data_part()
                if trigger.incident != incident:
                    raise RuntimeError("outbox Incident snapshot binding mismatch")
                transaction.insert(
                    "CanonicalIncidentOutboxV2",
                    columns=_OUTBOX_COLUMNS,
                    values=(
                        (
                            outbox_event_id,
                            incident.incident_id,
                            normalized.source_event_id,
                            trigger.message_type,
                            json_object(json_safe(payload)),
                            "PENDING",
                            0,
                            processed_at,
                            processed_at,
                            None,
                            None,
                            None,
                            None,
                        ),
                    ),
                )
            result = IngestResult(
                disposition=disposition,
                source_event_id=normalized.source_event_id,
                trace_id=normalized.trace_id,
                incident=incident,
                source_association=source_association,
                outbox_event_id=outbox_event_id,
            )
            self._insert_inbox_tx(
                transaction,
                normalized,
                envelope_fingerprint=envelope_fingerprint,
                result=result,
                processed_at=processed_at,
            )
            return result

        return await asyncio.to_thread(self._database.run_in_transaction, callback)

    @staticmethod
    def _incident_identity(incident: Incident) -> tuple[object, ...]:
        """Fields that cannot legally change over an Incident lifecycle."""

        return (
            incident.incident_id,
            incident.correlation_key,
            incident.technology,
            incident.detected_at,
            incident.created_at,
            incident.trace_id,
            incident.source_event_ids,
        )

    @staticmethod
    def _validate_trigger_template(
        trigger: IncidentTrigger,
        *,
        envelope: SourceEventEnvelope,
        incident: Incident,
        idempotency_key: str,
    ) -> None:
        if (
            trigger.incident != incident
            or trigger.incident_id != incident.incident_id
            or trigger.trace_id != envelope.trace_id
            or trigger.idempotency_key != idempotency_key
            or trigger.sent_at != envelope.received_at
            or envelope.source_event_id not in trigger.incident.source_event_ids
        ):
            raise ValueError("outbox IncidentTrigger binding mismatch")

    @classmethod
    def _materialize_trigger(
        cls,
        template: IncidentTrigger | None,
        *,
        envelope: SourceEventEnvelope,
        incident: Incident,
        idempotency_key: str,
    ) -> IncidentTrigger:
        if template is None:
            trigger = IncidentTrigger(
                message_id=cls._contract_id("message", envelope.source_event_id),
                workflow_id=cls._contract_id(
                    "workflow", envelope.source_event_id
                ),
                incident_id=incident.incident_id,
                trace_id=envelope.trace_id,
                idempotency_key=idempotency_key,
                sent_at=envelope.received_at,
                incident=incident,
                summary_zh="检测到实时故障并提交 Canonical Incident。",
            )
        else:
            payload = template.model_dump(mode="python", round_trip=True)
            payload.update(
                incident_id=incident.incident_id,
                trace_id=envelope.trace_id,
                idempotency_key=idempotency_key,
                incident=incident,
            )
            trigger = IncidentTrigger.model_validate(payload)
        if (
            trigger.incident != incident
            or trigger.trace_id != envelope.trace_id
            or trigger.idempotency_key != idempotency_key
        ):
            raise RuntimeError("materialized IncidentTrigger binding mismatch")
        return trigger

    @staticmethod
    def _contract_id(kind: str, source_event_id: str) -> str:
        digest = hashlib.sha256(
            f"{kind}\0{source_event_id}".encode("utf-8")
        ).hexdigest()
        return f"{kind}-{digest[:48]}"

    @staticmethod
    def _event_idempotency_key(source_event_id: str) -> str:
        digest = hashlib.sha256(source_event_id.encode("utf-8")).hexdigest()
        return f"source-event-{digest}"

    @staticmethod
    def _outbox_event_id(source_event_id: str, incident_id: str) -> str:
        digest = hashlib.sha256(
            f"incident-trigger\0{source_event_id}\0{incident_id}".encode("utf-8")
        ).hexdigest()
        return f"outbox-{digest[:48]}"

    @staticmethod
    def _insert_inbox_tx(
        transaction: Any,
        envelope: SourceEventEnvelope,
        *,
        envelope_fingerprint: str,
        result: IngestResult,
        processed_at: datetime,
    ) -> None:
        assert_safe(result, boundary="ingest-result")
        transaction.insert(
            "CanonicalSourceEventInboxV2",
            columns=_INBOX_COLUMNS,
            values=(
                (
                    envelope.source_event_id,
                    envelope.source,
                    envelope.event_type,
                    envelope.payload_sha256,
                    envelope_fingerprint,
                    envelope.trace_id,
                    envelope.received_at,
                    processed_at,
                    result.disposition.value,
                    None if result.incident is None else result.incident.incident_id,
                    result.outbox_event_id,
                    json_object(json_safe(result)),
                ),
            ),
        )


__all__ = [
    "IngestDisposition",
    "IngestResult",
    "SourceEventEnvelope",
    "SpannerEventIngestRepository",
]
