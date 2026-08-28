"""Cloud KPI detection composition built over shared deterministic logic."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telco_domain.contracts import IncidentTrigger
from telco_domain.models import Incident

from ._common import Clock, assert_safe, require_non_empty


class CloudKpiDetectionService:
    """Separate zero-write scans from explicit, bounded Incident persistence."""

    def __init__(
        self,
        rules_dir: str | Path,
        *,
        incident_repository: Any,
        telemetry_repository: Any,
        clock: Clock | None = None,
        max_candidates: int = 100,
        max_writes: int = 100,
    ) -> None:
        if max_candidates < 1 or max_candidates > 100:
            raise ValueError("max_candidates must be between 1 and 100")
        if max_writes < 1 or max_writes > 100:
            raise ValueError("max_writes must be between 1 and 100")
        resolved_rules = Path(rules_dir).expanduser().resolve(strict=False)
        if not resolved_rules.is_dir():
            raise FileNotFoundError(resolved_rules)

        # Repository-only Cloud images do not carry DuckDB or local assets.
        # Detector jobs opt into this deterministic composition explicitly.
        try:
            from telco_local.detector import LocalDetector
            from telco_local.rules import JsonRuleRepository
        except ImportError:
            raise RuntimeError(
                "Cloud KPI detection requires the telco-cloud[detector] extra"
            ) from None

        effective_clock = clock or (lambda: datetime.now(UTC))
        self._incidents = incident_repository
        self._max_candidates = max_candidates
        self._max_writes = max_writes
        self._detector = LocalDetector(
            None,
            rule_repository=JsonRuleRepository(resolved_rules),
            incident_repository=incident_repository,
            telemetry_repository=telemetry_repository,
            clock=effective_clock,
        )

    async def scan(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        resource_ids: Sequence[str],
        trace_id: str,
        workflow_id: str,
    ) -> tuple[IncidentTrigger, ...]:
        """Return deterministic candidates without writing any Incident row."""

        candidates = tuple(
            await self._detector.scan(
                trace_id,
                workflow_id=workflow_id,
                window_start=window_start,
                window_end=window_end,
                resource_ids=resource_ids,
            )
        )
        if len(candidates) > self._max_candidates:
            raise RuntimeError(
                f"cloud detector candidate capacity exceeded {self._max_candidates}"
            )
        assert_safe(candidates, boundary="cloud-kpi-candidates")
        return candidates

    async def scan_and_correlate(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        resource_ids: Sequence[str],
        trace_id: str,
        workflow_id: str,
        actor: str = "cloud-kpi-detector",
        reason: str = "scheduled KPI detection",
    ) -> tuple[Incident, ...]:
        """Explicitly persist every candidate after a complete bounded scan."""

        require_non_empty("actor", actor, max_length=256)
        require_non_empty("reason", reason, max_length=4_096)
        normalized_workflow = require_non_empty(
            "workflow_id", workflow_id, max_length=256
        )
        candidates = await self.scan(
            window_start=window_start,
            window_end=window_end,
            resource_ids=resource_ids,
            trace_id=trace_id,
            workflow_id=normalized_workflow,
        )
        if len(candidates) > self._max_writes:
            raise RuntimeError(
                f"cloud detector write capacity exceeded {self._max_writes}"
            )

        persisted: list[Incident] = []
        for candidate in candidates:
            digest = hashlib.sha256(
                f"cloud-kpi\0{normalized_workflow}\0{candidate.incident_id}".encode(
                    "utf-8"
                )
            ).hexdigest()
            persisted.append(
                await self._incidents.create_or_correlate(
                    candidate.incident,
                    idempotency_key=f"cloud-kpi-{digest}",
                    actor=actor,
                    reason=reason,
                    trace_id=candidate.trace_id,
                )
            )
        result = tuple(persisted)
        assert_safe(result, boundary="cloud-kpi-persisted")
        return result


__all__ = ["CloudKpiDetectionService"]
