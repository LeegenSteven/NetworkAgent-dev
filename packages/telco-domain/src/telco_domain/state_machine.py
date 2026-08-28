"""Pure state transitions for the canonical :class:`Incident` aggregate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .models import (
    ActionRunStatus,
    ApprovalStatus,
    ApprovalType,
    Incident,
    IncidentStatus,
    RcaConclusion,
    ReportStatus,
    VerificationStatus,
)


class IncidentStateError(ValueError):
    """Base class for deterministic incident state-machine failures."""


class InvalidTransitionError(IncidentStateError):
    def __init__(
        self,
        incident_id: str,
        current_status: IncidentStatus,
        target_status: IncidentStatus,
    ) -> None:
        self.incident_id = incident_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"incident {incident_id!r} cannot transition from "
            f"{current_status.value} to {target_status.value}"
        )


class RevisionConflictError(IncidentStateError):
    def __init__(self, incident_id: str, expected: int, actual: int) -> None:
        self.incident_id = incident_id
        self.expected_revision = expected
        self.actual_revision = actual
        super().__init__(
            f"incident {incident_id!r} revision conflict: "
            f"expected {expected}, actual {actual}"
        )


class InvalidTransitionUpdateError(IncidentStateError):
    def __init__(self, protected_fields: set[str]) -> None:
        self.protected_fields = frozenset(protected_fields)
        rendered = ", ".join(sorted(protected_fields))
        super().__init__(f"transition updates cannot replace protected fields: {rendered}")


class TransitionTimeError(IncidentStateError):
    def __init__(self, transitioned_at: datetime, current_updated_at: datetime) -> None:
        self.transitioned_at = transitioned_at
        self.current_updated_at = current_updated_at
        super().__init__(
            "transitioned_at must include a timezone and must not be earlier "
            "than the incident updated_at"
        )


class TransitionGuardError(IncidentStateError):
    def __init__(
        self, incident_id: str, target_status: IncidentStatus, reason: str
    ) -> None:
        self.incident_id = incident_id
        self.target_status = target_status
        self.reason = reason
        super().__init__(
            f"incident {incident_id!r} cannot enter {target_status.value}: {reason}"
        )


# These aliases make the error names self-describing without creating distinct
# exception types that callers would have to catch separately.
InvalidIncidentTransitionError = InvalidTransitionError
IncidentRevisionConflictError = RevisionConflictError


_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.DETECTED: frozenset(
        {
            IncidentStatus.TRIAGED,
            IncidentStatus.DUPLICATE,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.TRIAGED: frozenset(
        {
            IncidentStatus.INVESTIGATING,
            IncidentStatus.DUPLICATE,
            IncidentStatus.REJECTED,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.INVESTIGATING: frozenset(
        {
            IncidentStatus.RCA_COMPLETE,
            IncidentStatus.DUPLICATE,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.RCA_COMPLETE: frozenset(
        {
            IncidentStatus.AWAITING_APPROVAL,
            IncidentStatus.INVESTIGATING,
            IncidentStatus.REJECTED,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.AWAITING_APPROVAL: frozenset(
        {
            IncidentStatus.REMEDIATING,
            IncidentStatus.REJECTED,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.REMEDIATING: frozenset(
        {
            IncidentStatus.VERIFYING,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.VERIFYING: frozenset(
        {
            IncidentStatus.RESOLVED,
            IncidentStatus.REOPENED,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
    IncidentStatus.RESOLVED: frozenset(
        {IncidentStatus.CLOSED, IncidentStatus.REOPENED}
    ),
    IncidentStatus.CLOSED: frozenset({IncidentStatus.REOPENED}),
    IncidentStatus.DUPLICATE: frozenset(),
    IncidentStatus.REJECTED: frozenset(),
    IncidentStatus.FAILED: frozenset(),
    IncidentStatus.CANCELLED: frozenset(),
    IncidentStatus.REOPENED: frozenset(
        {
            IncidentStatus.INVESTIGATING,
            IncidentStatus.FAILED,
            IncidentStatus.CANCELLED,
        }
    ),
}


# A terminal status has no outgoing edge. CLOSED is settled but explicitly
# reopenable, so it must not be reported as terminal.
TERMINAL_STATUSES = frozenset(
    {
        IncidentStatus.DUPLICATE,
        IncidentStatus.REJECTED,
        IncidentStatus.FAILED,
        IncidentStatus.CANCELLED,
    }
)
CLOSED_STATUSES = TERMINAL_STATUSES | {IncidentStatus.CLOSED}
SETTLED_STATUSES = frozenset(
    CLOSED_STATUSES
)
REOPENABLE_STATUSES = frozenset(
    {
        IncidentStatus.VERIFYING,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    }
)
ACTIVE_STATUSES = frozenset(set(IncidentStatus) - set(SETTLED_STATUSES))


def allowed_transitions(
    status: IncidentStatus | str,
) -> frozenset[IncidentStatus]:
    """Return the immutable set of legal direct successors for ``status``."""

    return _TRANSITIONS[IncidentStatus(status)]


def can_transition(
    current_status: IncidentStatus | str,
    target_status: IncidentStatus | str,
) -> bool:
    """Return whether a direct transition is present in the explicit graph."""

    return IncidentStatus(target_status) in allowed_transitions(current_status)


_PROTECTED_UPDATE_FIELDS = {
    "schema_version",
    "incident_id",
    "correlation_key",
    "technology",
    "status",
    "revision",
    "created_at",
    "detected_at",
    "trace_id",
    "updated_at",
}

_APPEND_ONLY_FIELDS = (
    "approvals",
    "rca_reports",
    "action_runs",
    "verification_runs",
    "evidence_refs",
    "source_event_ids",
)

_FROZEN_RECOMMENDATION_STATUSES = {
    IncidentStatus.RCA_COMPLETE,
    IncidentStatus.AWAITING_APPROVAL,
    IncidentStatus.REMEDIATING,
    IncidentStatus.VERIFYING,
    IncidentStatus.RESOLVED,
    IncidentStatus.CLOSED,
}


def _normalize_transition_time(
    value: datetime | None, current_updated_at: datetime
) -> datetime:
    if value is None:
        return max(datetime.now(UTC), current_updated_at)
    if value.tzinfo is None or value.utcoffset() is None:
        raise TransitionTimeError(value, current_updated_at)
    normalized = value.astimezone(UTC)
    if normalized < current_updated_at:
        raise TransitionTimeError(normalized, current_updated_at)
    return normalized


def _normalize_trusted_now(value: datetime | None) -> datetime:
    instant = datetime.now(UTC) if value is None else value
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise IncidentStateError("now must include a timezone")
    return instant.astimezone(UTC)


def _raise_guard(incident: Incident, target: IncidentStatus, reason: str) -> None:
    raise TransitionGuardError(incident.incident_id, target, reason)


def _latest_report(incident: Incident):
    if not incident.rca_reports:
        return None
    return max(incident.rca_reports, key=lambda report: report.version)


def _validate_append_only(original: Incident, successor: Incident) -> None:
    for field in _APPEND_ONLY_FIELDS:
        before = tuple(getattr(original, field))
        after = tuple(getattr(successor, field))
        if len(after) < len(before) or after[: len(before)] != before:
            _raise_guard(
                successor,
                successor.status,
                f"{field} is append-only; existing history cannot be removed or changed",
            )
    if (
        original.status in _FROZEN_RECOMMENDATION_STATUSES
        and successor.recommendations != original.recommendations
    ):
        _raise_guard(
            successor,
            successor.status,
            "recommendations are frozen after entering AWAITING_APPROVAL",
        )


def _validate_requested_history_update(
    original: Incident,
    target: IncidentStatus,
    changes: Mapping[str, Any],
) -> None:
    """Reject history rewrites before Pydantic can collapse them into another error."""

    for field in _APPEND_ONLY_FIELDS:
        if field not in changes:
            continue
        before = tuple(getattr(original, field))
        after = tuple(changes[field])
        if len(after) < len(before) or after[: len(before)] != before:
            _raise_guard(
                original,
                target,
                f"{field} is append-only; existing history cannot be removed or changed",
            )
    if (
        original.status in _FROZEN_RECOMMENDATION_STATUSES
        and "recommendations" in changes
        and tuple(changes["recommendations"]) != original.recommendations
    ):
        _raise_guard(
            original,
            target,
            "recommendations are frozen after entering AWAITING_APPROVAL",
        )


def _validate_rca_complete(incident: Incident, target: IncidentStatus) -> None:
    report = _latest_report(incident)
    if report is None:
        _raise_guard(incident, target, "an eligible latest RCA report is required")
    eligible_statuses = {
        ReportStatus.PROPOSED,
        ReportStatus.APPROVED,
        ReportStatus.PERSISTED,
    }
    has_conclusion = bool((report.root_cause or "").strip()) or (
        report.conclusion is RcaConclusion.INCONCLUSIVE
    )
    if (
        report.incident_id != incident.incident_id
        or report.status not in eligible_statuses
        or not report.evidence_refs
        or not has_conclusion
        or report.recommendations != incident.recommendations
    ):
        _raise_guard(
            incident,
            target,
            "latest RCA report must match the Incident, be eligible, include evidence, "
            "declare a root cause or INCONCLUSIVE result, and own recommendations",
        )


def _latest_approval_events(incident: Incident):
    latest = {}
    for decision in incident.approvals:
        previous = latest.get(decision.request_id)
        if previous is None or decision.sequence > previous.sequence:
            latest[decision.request_id] = decision
    return tuple(latest.values())


def _validate_remediation_approvals(
    incident: Incident,
    target: IncidentStatus,
    now: datetime,
) -> None:
    report = _latest_report(incident)
    if report is None or report.recommendations != incident.recommendations:
        _raise_guard(
            incident,
            target,
            "latest RCA report must own the frozen remediation actions",
        )
    latest_decisions = _latest_approval_events(incident)
    for action in incident.recommendations:
        approved = any(
            decision.status is ApprovalStatus.APPROVED
            and decision.covers_action(action, incident.incident_id, report)
            and decision.is_effective(now)
            for decision in latest_decisions
        )
        if not approved:
            _raise_guard(
                incident,
                target,
                f"action {action.action_id!r} lacks a matching effective approval",
            )


def _successful_action_runs(
    incident: Incident,
    target: IncidentStatus,
):
    successful = []
    for action in incident.recommendations:
        candidates = [
            run for run in incident.action_runs if run.action_id == action.action_id
        ]
        if not candidates:
            _raise_guard(
                incident,
                target,
                "every recommended action requires an exact successful ActionRun",
            )
        latest = max(candidates, key=lambda run: run.attempt)
        if (
            latest.status is not ActionRunStatus.SUCCEEDED
            or latest.incident_id != incident.incident_id
            or latest.action_hash != action.action_hash
            or latest.finished_at is None
        ):
            _raise_guard(
                incident,
                target,
                "every recommended action requires an exact successful ActionRun",
            )
        successful.append(latest)
    return tuple(successful)


def _validate_transition_guard(
    original: Incident,
    incident: Incident,
    target: IncidentStatus,
    now: datetime,
) -> None:
    _validate_append_only(original, incident)

    if target is IncidentStatus.DUPLICATE and incident.duplicate_of is None:
        _raise_guard(incident, target, "duplicate_of is required")

    if target is IncidentStatus.RCA_COMPLETE:
        _validate_rca_complete(incident, target)

    if target is IncidentStatus.AWAITING_APPROVAL:
        _validate_rca_complete(incident, target)
        if not incident.recommendations:
            _raise_guard(incident, target, "at least one remediation action is required")

    if target is IncidentStatus.REMEDIATING:
        if not incident.recommendations:
            _raise_guard(incident, target, "at least one remediation action is required")
        _validate_remediation_approvals(incident, target, now)

    if target is IncidentStatus.VERIFYING:
        _successful_action_runs(incident, target)

    if target is IncidentStatus.RESOLVED:
        successful_runs = _successful_action_runs(incident, target)
        if not incident.verification_runs:
            _raise_guard(incident, target, "a latest VerificationRun is required")
        latest = incident.verification_runs[-1]
        successful_ids = {run.action_run_id for run in successful_runs}
        if (
            latest.status is not VerificationStatus.PASSED
            or latest.incident_id != incident.incident_id
            or latest.finished_at is None
            or not any(check.strip() for check in latest.checks)
            or not latest.evidence_refs
            or successful_ids != set(latest.action_run_ids)
        ):
            _raise_guard(
                incident,
                target,
                "latest VerificationRun must be PASSED, complete, and cover successful runs",
            )


def transition_incident(
    incident: Incident,
    target_status: IncidentStatus | str,
    expected_revision: int,
    *,
    transitioned_at: datetime | None = None,
    now: datetime | None = None,
    updates: Mapping[str, Any] | None = None,
) -> Incident:
    """Return a validated successor without mutating ``incident``.

    The revision comparison deliberately occurs before graph validation so a
    stale caller cannot learn or act on state it did not read.  Repository-level
    idempotency may return a previously committed successor before invoking this
    pure function.
    """

    if expected_revision != incident.revision:
        raise RevisionConflictError(
            incident.incident_id, expected_revision, incident.revision
        )

    try:
        normalized_target = IncidentStatus(target_status)
    except ValueError as exc:
        raise IncidentStateError(f"unknown incident status: {target_status!r}") from exc

    if not can_transition(incident.status, normalized_target):
        raise InvalidTransitionError(
            incident.incident_id, incident.status, normalized_target
        )

    changes = dict(updates or {})
    protected = _PROTECTED_UPDATE_FIELDS.intersection(changes)
    if protected:
        raise InvalidTransitionUpdateError(protected)
    _validate_requested_history_update(incident, normalized_target, changes)

    normalized_time = _normalize_transition_time(
        transitioned_at, incident.updated_at
    )
    trusted_now = _normalize_trusted_now(now)
    payload = incident.model_dump(mode="python")
    payload.update(changes)
    payload.update(
        {
            "status": normalized_target,
            "revision": incident.revision + 1,
            "updated_at": normalized_time,
        }
    )
    successor = Incident.model_validate(payload)
    _validate_transition_guard(
        incident,
        successor,
        normalized_target,
        trusted_now,
    )
    return successor


def transition(
    incident: Incident,
    target_status: IncidentStatus | str,
    expected_revision: int,
    **kwargs: Any,
) -> Incident:
    """Concise alias for :func:`transition_incident`."""

    return transition_incident(
        incident,
        target_status,
        expected_revision,
        **kwargs,
    )
