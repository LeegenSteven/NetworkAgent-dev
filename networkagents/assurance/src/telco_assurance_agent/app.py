"""Explicit Assurance bootstrap and side-effect-free runtime composition."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from telco_local import LocalGovernanceEngine, LocalProfile

from .boundary import SafeA2ARequestBoundary
from .business_boundary import LocalBusinessOperationBoundary, LocalHttpRequestAdmission
from .card import build_agent_card
from .config import AssuranceConfig
from .executor import AssuranceAgentExecutor
from .fault_receiver import LocalReplayFaultReceiver, fault_receiver_routes
from .governance_http import governance_routes
from .service import AfterIncidentWriteHook, AssuranceService, Clock
from .status_http import LocalNotFoundApplication, local_not_found, status_routes
from .stores import (
    DuckDbPendingConfirmationStore,
    DuckDbTaskStore,
    initialize_assurance_database,
)


@dataclass(frozen=True, slots=True)
class AssuranceComponents:
    config: AssuranceConfig
    profile: LocalProfile
    business_profile: LocalProfile
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
    business_profile = LocalProfile.open_existing(
        config.local_profile_config,
        clock=clock,
    )
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
        business_profile.incident_repository,
        business_profile.rca_gateway,
        clock=clock or (lambda: datetime.now(UTC)),
    )
    fault_receiver = LocalReplayFaultReceiver(
        business_profile.incident_repository,
        business_profile.rule_repository,
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
        business_profile=business_profile,
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
    business_boundary = LocalBusinessOperationBoundary()
    request_admission = LocalHttpRequestAdmission()
    a2a_boundary = SafeA2ARequestBoundary(
        sdk_application,
        request_admission=request_admission,
        operation_boundary=business_boundary,
    )

    @asynccontextmanager
    async def lifespan(_application: Starlette):
        try:
            yield
        finally:
            await business_boundary.aclose()

    application = Starlette(
        lifespan=lifespan,
        routes=[
            *status_routes(
                components.profile.incident_repository,
                operation_boundary=business_boundary,
            ),
            *fault_receiver_routes(
                components.fault_receiver,
                operation_boundary=business_boundary,
                request_admission=request_admission,
            ),
            *governance_routes(
                components.governance_engine,
                operation_boundary=business_boundary,
                request_admission=request_admission,
            ),
            Route(
                "/local/v1",
                endpoint=local_not_found,
                methods=(
                    "GET",
                    "HEAD",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "OPTIONS",
                    "TRACE",
                    "CONNECT",
                ),
                name="local-service-root-not-found",
            ),
            Mount(
                "/local/v1",
                app=LocalNotFoundApplication(),
                name="local-service-fallback",
            ),
            Mount("/", app=a2a_boundary),
        ],
    )
    # Public audit/test seam: callers can inspect durable stores without
    # reaching into the A2A SDK handler's private attributes.
    application.state.assurance_components = components
    application.state.assurance_service = components.service
    application.state.assurance_pending_store = components.pending_store
    application.state.assurance_task_store = components.task_store
    application.state.local_fault_receiver = components.fault_receiver
    application.state.local_governance_engine = components.governance_engine
    application.state.local_business_operation_boundary = business_boundary
    application.state.local_http_request_admission = request_admission
    application.state.local_a2a_request_admission = a2a_boundary.request_admission
    return application


__all__ = [
    "AssuranceComponents",
    "build_components",
    "create_app",
    "initialize_assurance",
]
