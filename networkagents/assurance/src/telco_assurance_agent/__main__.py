"""Command-line entry point with separate ``init`` and ``run`` phases."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import uvicorn

from .app import create_app, initialize_assurance
from .config import AssuranceConfig
from .transport_http import BoundedH11Protocol


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telco-assurance-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "run"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--database", type=Path, required=True)
        subparser.add_argument("--performance-csv", type=Path, required=True)
        subparser.add_argument("--safe-trace-csv", type=Path, required=True)
        subparser.add_argument("--rules-dir", type=Path, required=True)
        subparser.add_argument("--documents-dir", type=Path)
        subparser.add_argument("--public-url", required=True)
        subparser.add_argument("--actor", default="local-assurance-service")
        subparser.add_argument("--challenge-ttl-seconds", type=int, default=600)
        subparser.add_argument("--pending-capacity", type=int, default=1_000)
        subparser.add_argument("--task-capacity", type=int, default=1_000)
        subparser.add_argument("--host", default="127.0.0.1")
        subparser.add_argument("--port", type=int, default=8085)
    subparsers.choices["init"].add_argument("--reset", action="store_true")
    return parser


def _config(arguments: argparse.Namespace) -> AssuranceConfig:
    return AssuranceConfig(
        database_path=arguments.database,
        performance_csv_path=arguments.performance_csv,
        safe_trace_csv_path=arguments.safe_trace_csv,
        rules_dir=arguments.rules_dir,
        documents_dir=arguments.documents_dir,
        source_timezone="UTC",
        public_url=arguments.public_url,
        actor=arguments.actor,
        challenge_ttl_seconds=arguments.challenge_ttl_seconds,
        pending_capacity=arguments.pending_capacity,
        task_capacity=arguments.task_capacity,
        host=arguments.host,
        port=arguments.port,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = _config(arguments)
    if arguments.command == "init":
        initialize_assurance(config, reset=arguments.reset)
        return 0
    application = create_app(config)
    uvicorn.run(
        application,
        host=config.host,
        port=config.port,
        workers=1,
        reload=False,
        interface="asgi3",
        lifespan="on",
        http=BoundedH11Protocol,
        ws="none",
        proxy_headers=False,
        forwarded_allow_ips="",
        access_log=False,
        server_header=False,
        date_header=False,
        limit_concurrency=None,
        backlog=16,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=10,
        h11_max_incomplete_event_size=16_384,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
