"""Explicit cloud composition root for the fault ingress service."""

from __future__ import annotations

import argparse
import logging
import os

from aiohttp import web

from .app import create_app
from .config import FaultIngressConfig, FaultPipelineMode


_EXPECTED_DATABASE_ROLE = "telco_fault_writer"


def _build_repository():
    from google.cloud import spanner
    from telco_cloud import CloudProfileConfig, SpannerEventIngestRepository

    profile = CloudProfileConfig.from_env()
    if profile.emulator_host != os.environ.get("SPANNER_EMULATOR_HOST"):
        raise RuntimeError("SPANNER_EMULATOR_HOST configuration mismatch")
    allowed_roles = {_EXPECTED_DATABASE_ROLE}
    if profile.emulator_host is not None:
        allowed_roles.add(None)
    if profile.database_role not in allowed_roles:
        raise RuntimeError("fault ingress Spanner database role mismatch")
    client = spanner.Client(project=profile.project_id)
    database = client.instance(profile.instance_id).database(
        profile.database_id,
        database_role=profile.database_role,
    )
    return SpannerEventIngestRepository(database)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telco-fault-ingress",
        description="Run the strict canonical Pub/Sub fault ingress service.",
    )
    return parser.parse_args()


def main() -> None:
    _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s::%(levelname)s::%(name)s::%(message)s",
    )
    config = FaultIngressConfig.from_env()
    if config.mode is FaultPipelineMode.LEGACY:
        raise RuntimeError(
            "legacy mode requires an explicitly injected legacy handler"
        )
    repository = _build_repository()
    web.run_app(create_app(config, repository), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
