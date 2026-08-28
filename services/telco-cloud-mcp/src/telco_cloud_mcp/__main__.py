"""Explicit Spanner and FastMCP composition root."""

from __future__ import annotations

import argparse
import logging
import os

from .config import CloudMcpConfig
from .server import create_server


_EXPECTED_DATABASE_ROLE = "telco_mcp_reader"


def _repositories():
    from google.cloud import spanner
    from telco_cloud import (
        CloudProfileConfig,
        SpannerIncidentRepository,
        SpannerTelemetryRepository,
    )

    profile = CloudProfileConfig.from_env()
    if profile.emulator_host != os.environ.get("SPANNER_EMULATOR_HOST"):
        raise RuntimeError("SPANNER_EMULATOR_HOST configuration mismatch")
    allowed_roles = {_EXPECTED_DATABASE_ROLE}
    if profile.emulator_host is not None:
        allowed_roles.add(None)
    if profile.database_role not in allowed_roles:
        raise RuntimeError("cloud MCP Spanner database role mismatch")
    client = spanner.Client(project=profile.project_id)
    database = client.instance(profile.instance_id).database(
        profile.database_id,
        database_role=profile.database_role,
    )
    return SpannerIncidentRepository(database), SpannerTelemetryRepository(database)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telco-cloud-mcp",
        description="Run the bounded read-only Telco Cloud MCP service.",
    )
    return parser.parse_args()


def main() -> None:
    _parse_args()
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s::%(levelname)s::%(name)s::%(message)s",
    )
    config = CloudMcpConfig.from_env()
    incidents, telemetry = _repositories()
    server = create_server(incidents, telemetry)
    app = server.http_app(transport="streamable-http", stateless_http=True)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
