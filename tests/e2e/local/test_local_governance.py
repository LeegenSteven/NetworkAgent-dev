"""Full-data Local Profile governance loop with simulation-only actions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from telco_domain import IncidentStatus, VerificationStatus, assert_model_safe
from telco_local import LocalGovernanceEngine, LocalProfile, LocalProfileConfig


ROOT = Path(__file__).resolve().parents[3]


def test_full_lte_assets_close_and_reopen_without_network_side_effects(tmp_path) -> None:
    async def scenario() -> None:
        profile = LocalProfile.initialize(
            LocalProfileConfig(
                database_path=tmp_path / "local-governance.duckdb",
                performance_csv_path=ROOT / "data/samples/lte-demo/performance.csv",
                safe_trace_csv_path=ROOT
                / "data/samples/lte-demo/safe-cell-traces.csv",
                rules_dir=ROOT / "data/rca-rules/lte",
                documents_dir=ROOT / "data/docs/lte",
                source_timezone="UTC",
            ),
            reset=True,
        )
        candidates = await profile.detector.scan(
            "trace-governance-detect",
            workflow_id="workflow-governance-detect",
        )
        assert len(candidates) == 15

        selected = tuple(sorted(candidates, key=lambda item: item.incident_id))[:2]
        incidents = []
        for index, trigger in enumerate(selected, start=1):
            incidents.append(
                await profile.detector.confirm(
                    trigger.incident_id,
                    trace_id=f"trace-governance-confirm-{index}",
                    idempotency_key=f"confirm-governance-{index}",
                    actor="local-e2e-operator",
                    reason="Explicitly confirm the isolated local simulation candidate",
                )
            )

        engine = LocalGovernanceEngine(
            profile.incident_repository,
            profile.rca_gateway,
            clock=lambda: datetime.now(UTC),
        )
        final_states = (IncidentStatus.RESOLVED, IncidentStatus.REOPENED)
        for index, (incident, final_state) in enumerate(
            zip(incidents, final_states, strict=True), start=1
        ):
            prepared = await engine.prepare(
                incident.incident_id,
                idempotency_key=f"prepare-governance-{index}",
            )
            assert prepared.incident.status is IncidentStatus.AWAITING_APPROVAL
            assert prepared.action is not None
            assert prepared.action.action_type == "LOCAL_SIMULATION"
            assert prepared.action.requires_approval is True
            assert prepared.action.reversible is True

            decided = await engine.decide(
                incident.incident_id,
                approve=True,
                expected_action_hash=prepared.action.action_hash,
                expected_revision=prepared.incident.revision,
                actor="local-e2e-operator",
                reason="Approve only the reviewed side-effect-free local simulation",
                idempotency_key=f"decision-governance-{index}",
            )
            assert decided.incident.status is IncidentStatus.REMEDIATING

            completed = await engine.execute(
                incident.incident_id,
                idempotency_key=f"execute-governance-{index}",
                verification_passed=final_state is IncidentStatus.RESOLVED,
            )
            assert completed.incident.status is final_state
            assert len(completed.incident.action_runs) == 1
            assert completed.incident.action_runs[0].metadata == {
                "mode": "simulation",
                "side_effects": False,
            }
            assert len(completed.incident.verification_runs) == 1
            expected_verification = (
                VerificationStatus.PASSED
                if final_state is IncidentStatus.RESOLVED
                else VerificationStatus.FAILED
            )
            assert completed.incident.verification_runs[0].status is expected_verification
            assert len(await profile.incident_repository.history(incident.incident_id)) == 8
            assert_model_safe(completed.incident)

            replay = await engine.execute(
                incident.incident_id,
                idempotency_key=f"execute-governance-{index}",
                verification_passed=final_state is IncidentStatus.RESOLVED,
            )
            assert replay.replayed is True
            assert replay.incident == completed.incident
            assert len(await profile.incident_repository.history(incident.incident_id)) == 8

        persisted = await profile.incident_repository.list()
        assert {item.status for item in persisted} == set(final_states)

    asyncio.run(scenario())
