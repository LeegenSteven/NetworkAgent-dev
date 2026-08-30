"""Explicit Assurance bootstrap and side-effect-free runtime composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from starlette.applications import Starlette
from starlette.routing import Mount
from telco_local import LocalGovernanceEngine, LocalProfile

from .boundary import SafeA2ARequestBoundary
from .card import build_agent_card
from .config import AssuranceConfig
from .executor import AssuranceAgentExecutor
from .fault_receiver import LocalReplayFaultReceiver, fault_receiver_routes
from .governance_http import governance_routes
from .service import AfterIncidentWriteHook, AssuranceService, Clock
from .status_http import status_routes
from .stores import (
    DuckDbPendingConfirmationStore,
    DuckDbTaskStore,
    initialize_assurance_database,
)


@dataclass(frozen=True, slots=True)
class AssuranceComponents:
    config: AssuranceConfig
    profile: LocalProfile
    pending_store: DuckDbPendingConfirmationStore
    task_store: DuckDbTaskStore
    service: AssuranceService
    fault_receiver: LocalReplayFaultReceiver
    governance_engine: LocalGovernanceEngine
    executor: AssuranceAgentExecutor
    request_handler: DefaultRequestHandler


def initialize_assurance(
    config: AssuranceConfig,
    *,
    reset: bool = False,
    clock: Clock | None = None,
) -> LocalProfile:
    """Explicitly import local sources and add the Assurance runtime schema."""

    profile = LocalProfile.initialize(
        config.local_profile_config, reset=reset, clock=clock
    )
    initialize_assurance_database(config.database_path, reset=reset)
    return profile


def build_components(
    config: AssuranceConfig,
    *,
    clock: Clock | None = None,
    after_incident_write: AfterIncidentWriteHook | None = None,
) -> AssuranceComponents:
    """Open only a fully initialized DB; this runtime path performs no DDL."""

    profile = LocalProfile.open_existing(config.local_profile_config, clock=clock)
    pending_store = DuckDbPendingConfirmationStore(
        config.database_path, capacity=config.pending_capacity
    )
    task_store = DuckDbTaskStore(
        config.database_path, capacity=config.task_capacity, clock=clock
    )
    service = AssuranceService(
        profile,
        pending_store,
        actor=config.actor,
        challenge_ttl_seconds=config.challenge_ttl_seconds,
        clock=clock,
        after_incident_write=after_incident_write,
    )
    governance_engine = LocalGovernanceEngine(
        profile.incident_repository,
        profile.rca_gateway,
        clock=clock or (lambda: datetime.now(UTC)),
    )
    fault_receiver = LocalReplayFaultReceiver(
        profile.incident_repository,
        profile.rule_repository,
        actor=config.actor,
    )
    executor = AssuranceAgentExecutor(service)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )
    return AssuranceComponents(
        config=config,
        profile=profile,
        pending_store=pending_store,
        task_store=task_store,
        service=service,
        fault_receiver=fault_receiver,
        governance_engine=governance_engine,
        executor=executor,
        request_handler=request_handler,
    )


def create_app(
    config: AssuranceConfig,
    *,
    clock: Clock | None = None,
    after_incident_write: AfterIncidentWriteHook | None = None,
) -> Starlette:
    components = build_components(
        config,
        clock=clock,
        after_incident_write=after_incident_write,
    )
    sdk_application = A2AStarletteApplication(
        agent_card=build_agent_card(config),
        http_handler=components.request_handler,
    ).build(
        agent_card_url="/.well-known/agent-card.json",
        rpc_url="/",
    )
    application = Starlette(
        routes=[
            *status_routes(components.profile.incident_repository),
            *fault_receiver_routes(components.fault_receiver),
            *governance_routes(components.governance_engine),
            Mount("/", app=SafeA2ARequestBoundary(sdk_application)),
        ]
    )
    # Public audit/test seam: callers can inspect durable stores without
    # reaching into the A2A SDK handler's private attributes.
    application.state.assurance_components = components
    application.state.assurance_service = components.service
    application.state.assurance_pending_store = components.pending_store
    application.state.assurance_task_store = components.task_store
    application.state.local_fault_receiver = components.fault_receiver
    application.state.local_governance_engine = components.governance_engine
    return application


__all__ = [
    "AssuranceComponents",
    "build_components",
    "create_app",
    "initialize_assurance",
]
