"""aiohttp application factory with fixed safe responses."""

from __future__ import annotations

import logging

from aiohttp import web

from .boundary import parse_pubsub_push
from .config import FaultIngressConfig
from .models import EventIngestRepository, LegacyHandler, PermanentIngressError
from .service import FaultIngressService


logger = logging.getLogger(__name__)
SERVICE_KEY = web.AppKey("fault_ingress_service", FaultIngressService)


def _error(status: int, code: str) -> web.Response:
    headers = {"Retry-After": "5"} if status == 503 else None
    return web.json_response(
        {"error": {"code": code}}, status=status, headers=headers
    )


def create_app(
    config: FaultIngressConfig,
    repository: EventIngestRepository,
    *,
    legacy_handler: LegacyHandler | None = None,
    clock=None,
) -> web.Application:
    """Create an app without connecting to Spanner or reading credentials."""

    service = FaultIngressService(
        config,
        repository,
        legacy_handler=legacy_handler,
        clock=clock,
    )
    app = web.Application(client_max_size=config.max_request_bytes)
    app[SERVICE_KEY] = service

    async def receive(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return _error(400, "PUBSUB_CONTENT_TYPE_INVALID")
        if (
            request.content_length is not None
            and request.content_length > config.max_request_bytes
        ):
            return _error(400, "PUBSUB_REQUEST_SIZE_INVALID")
        try:
            body = await request.read()
            push = parse_pubsub_push(body, config)
            decision = await service.process(push)
        except web.HTTPRequestEntityTooLarge:
            return _error(400, "PUBSUB_REQUEST_SIZE_INVALID")
        except PermanentIngressError as exc:
            logger.warning("fault event permanently rejected code=%s", exc.code)
            return _error(400, exc.code)
        except Exception:
            logger.error("fault ingress request failed")
            return _error(503, "FAULT_INGRESS_UNAVAILABLE")
        if decision.http_status == 204:
            return web.Response(status=204)
        return _error(decision.http_status, decision.code)

    async def health(_: web.Request) -> web.Response:
        return web.json_response(
            {"status": "healthy", "service": "telco-fault-ingress"}
        )

    app.router.add_post("/events/pubsub", receive)
    app.router.add_get("/health", health)
    return app


__all__ = ["create_app"]
