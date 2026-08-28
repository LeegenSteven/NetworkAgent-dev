"""Explicit Assurance bootstrap and side-effect-free runtime composition."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from starlette.applications import Starlette
from telco_local import LocalProfile

from .boundary import SafeA2ARequestBoundary
from .card import build_agent_card
from .config import AssuranceConfig
from .executor import AssuranceAgentExecutor
from .service import AfterIncidentWriteHook, AssuranceService, Clock
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
    application = Starlette()
    application.mount("/", SafeA2ARequestBoundary(sdk_application))
    # Public audit/test seam: callers can inspect durable stores without
    # reaching into the A2A SDK handler's private attributes.
    application.state.assurance_components = components
    application.state.assurance_service = components.service
    application.state.assurance_pending_store = components.pending_store
    application.state.assurance_task_store = components.task_store
    return application


__all__ = [
    "AssuranceComponents",
    "build_components",
    "create_app",
    "initialize_assurance",
]
