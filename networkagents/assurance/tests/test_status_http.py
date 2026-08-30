from __future__ import annotations

import asyncio
import threading
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import httpx
from starlette.applications import Starlette

from telco_assurance_agent import AssuranceConfig, create_app, initialize_assurance
from telco_assurance_agent.business_boundary import LocalBusinessOperationBoundary
from telco_assurance_agent import PACKAGE_VERSION
from telco_assurance_agent.status_http import status_routes


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def test_package_version_matches_project_metadata() -> None:
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]
    assert project["version"] == PACKAGE_VERSION == "0.1.0"


def _config(tmp_path: Path) -> AssuranceConfig:
    return AssuranceConfig(
        database_path=tmp_path / "local.duckdb",
        performance_csv_path=REPOSITORY_ROOT / "data/samples/lte-demo/performance.csv",
        safe_trace_csv_path=REPOSITORY_ROOT
        / "data/samples/lte-demo/safe-cell-traces.csv",
        rules_dir=REPOSITORY_ROOT / "data/rca-rules/lte",
        public_url="http://127.0.0.1:8085/",
        actor="local-assurance-service",
    )


class _ReadinessRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[object, int, int]] = []

    async def list(self, *, status=None, limit: int = 100, offset: int = 0):
        self.calls.append((status, limit, offset))
        if self.fail:
            raise RuntimeError("private database path must not escape")
        return ()


class _BlockingReadinessRepository(_ReadinessRepository):
    async def list(self, *, status=None, limit: int = 100, offset: int = 0):
        self.calls.append((status, limit, offset))
        time.sleep(1.6)
        return ()


def test_real_app_exposes_loopback_health_ready_and_version_without_writes(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    profile = initialize_assurance(config, clock=lambda: NOW)
    app = create_app(config, clock=lambda: NOW)

    async def scenario() -> None:
        before = await profile.incident_repository.list(limit=1, offset=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as client:
            health = await client.get("/local/v1/healthz")
            assert health.status_code == 200
            assert health.json() == {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "status": "alive",
                },
            }

            ready = await client.get("/local/v1/readyz")
            assert ready.status_code == 200
            assert ready.json() == {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "status": "ready",
                    "profile": "local",
                    "repository": "ready",
                },
            }

            version = await client.get("/local/v1/version")
            assert version.status_code == 200
            assert version.json() == {
                "ok": True,
                "data": {
                    "service": "telco-assurance-agent",
                    "package_version": "0.1.0",
                    "local_http_api_version": "1.0",
                    "replay_schema_version": "1.0",
                    "domain_schema_version": "1.0",
                },
            }
            for response in (health, ready, version):
                assert response.headers["content-type"] == "application/json"
                assert "access-control-allow-origin" not in response.headers
                assert len(response.content) < 4096

            unknown = await client.get("/local/v1/not-a-public-route")
            assert unknown.status_code == 404
            assert unknown.json()["error"] == {
                "code": "LOCAL_SERVICE_NOT_FOUND",
                "message": "The local service route was not found.",
            }

            trailing_slash = await client.get("/local/v1/faults/replay/")
            assert trailing_slash.status_code == 404
            assert trailing_slash.headers["content-type"] == "application/json"
            assert "location" not in trailing_slash.headers

            extended_method = await client.request(
                "PROPFIND",
                "/local/v1/faults/replay",
            )
            assert extended_method.status_code == 404
            assert extended_method.json()["error"]["code"] == (
                "LOCAL_SERVICE_NOT_FOUND"
            )

        after = await profile.incident_repository.list(limit=1, offset=0)
        assert after == before == ()

    asyncio.run(scenario())


def test_status_routes_reject_non_loopback_query_and_dependency_failure() -> None:
    healthy = _ReadinessRepository()
    failing = _ReadinessRepository(fail=True)
    healthy_app = Starlette(routes=[*status_routes(healthy)])
    failing_app = Starlette(routes=[*status_routes(failing)])

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=healthy_app,
                client=("203.0.113.10", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as public_peer:
            response = await public_peer.get("/local/v1/healthz")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "LOCAL_SERVICE_BAD_HOST"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=healthy_app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://example.test",
        ) as public_host:
            response = await public_host.get("/local/v1/version")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "LOCAL_SERVICE_BAD_HOST"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=healthy_app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as client:
            for path in (
                "/local/v1/healthz?detail=private",
                "/local/v1/readyz?detail=private",
                "/local/v1/version?detail=private",
            ):
                response = await client.get(path)
                assert response.status_code == 422
                assert (
                    response.json()["error"]["code"] == "LOCAL_SERVICE_INVALID_REQUEST"
                )
            assert healthy.calls == []

            for method in (
                "HEAD",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "OPTIONS",
                "TRACE",
                "CONNECT",
            ):
                response = await client.request(method, "/local/v1/readyz")
                assert response.status_code == 405
                assert response.headers["allow"] == "GET"
                assert "access-control-allow-origin" not in response.headers
                assert response.headers["content-type"] == "application/json"
                if method != "HEAD":
                    assert response.json()["error"]["code"] == (
                        "LOCAL_SERVICE_METHOD_NOT_ALLOWED"
                    )
            assert healthy.calls == []

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=failing_app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as client:
            response = await client.get("/local/v1/readyz")
            assert response.status_code == 503
            assert response.json() == {
                "ok": False,
                "error": {
                    "code": "LOCAL_SERVICE_NOT_READY",
                    "message": "The local service is not ready.",
                },
            }
            assert "private database path" not in response.text
            assert failing.calls == [(None, 1, 0)]

    asyncio.run(scenario())


def test_readiness_timeout_bounds_a_synchronous_duckdb_style_probe() -> None:
    repository = _BlockingReadinessRepository()
    app = Starlette(routes=[*status_routes(repository)])

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as client:
            started = time.monotonic()
            response = await client.get("/local/v1/readyz")
            elapsed = time.monotonic() - started
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "LOCAL_SERVICE_NOT_READY"
            assert elapsed < 1.3

            repeated = await client.get("/local/v1/readyz")
            assert repeated.status_code == 503
            assert repository.calls == [(None, 1, 0)]
            await asyncio.sleep(0.7)

    asyncio.run(scenario())


def test_readiness_refuses_to_probe_while_the_business_worker_is_occupied() -> None:
    repository = _ReadinessRepository()
    boundary = LocalBusinessOperationBoundary(deadline_seconds=1.0)
    worker_started = threading.Event()
    release_worker = threading.Event()
    app = Starlette(
        routes=[
            *status_routes(
                repository,
                operation_boundary=boundary,
            )
        ]
    )

    async def blocked_operation() -> str:
        worker_started.set()
        if not release_worker.wait(timeout=2.0):
            raise RuntimeError("test worker release timed out")
        return "settled"

    async def scenario() -> None:
        running = asyncio.create_task(boundary.run(blocked_operation))
        assert await asyncio.to_thread(worker_started.wait, 1.0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                client=("127.0.0.1", 43120),
            ),
            base_url="http://127.0.0.1:8085",
        ) as client:
            ready = await client.get("/local/v1/readyz")
            assert ready.status_code == 503
            assert ready.json()["error"]["code"] == "LOCAL_SERVICE_NOT_READY"
            assert repository.calls == []

            health = await client.get("/local/v1/healthz")
            assert health.status_code == 200

        release_worker.set()
        assert await running == "settled"

    asyncio.run(scenario())
