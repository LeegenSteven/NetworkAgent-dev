"""Explicit, safe-JSON command line entry points for the Local Profile."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from telco_domain import IncidentNotFoundError, RcaRequest, assert_model_safe

from .config import LocalProfileConfig
from .profile import LocalProfile


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(f"invalid command arguments: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="telco-local",
        description="Deterministic LTE Local Profile assurance workflow",
    )
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--performance-csv-path", type=Path, required=True)
    parser.add_argument("--safe-trace-csv-path", type=Path, required=True)
    parser.add_argument("--rules-dir", type=Path, required=True)
    parser.add_argument("--documents-dir", type=Path)
    parser.add_argument("--source-timezone", required=True, choices=("UTC",))

    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="initialize the selected database")
    initialize.add_argument(
        "--reset",
        action="store_true",
        help="delete and rebuild only the explicitly selected local database",
    )

    detect = commands.add_parser("detect", help="preview candidates without writes")
    detect.add_argument("--trace-id", required=True)
    detect.add_argument("--workflow-id", required=True)

    confirm = commands.add_parser("confirm", help="persist one revalidated candidate")
    confirm.add_argument("candidate_id")
    confirm.add_argument("--trace-id", required=True)
    confirm.add_argument("--idempotency-key", required=True)
    confirm.add_argument("--actor", required=True)
    confirm.add_argument("--reason", required=True)

    analyze = commands.add_parser(
        "analyze",
        aliases=("rca",),
        help="generate a read-only RCA result for a stored Incident",
    )
    analyze.add_argument("incident_id")
    analyze.add_argument("--trace-id", required=True)
    analyze.add_argument("--workflow-id", required=True)
    analyze.add_argument("--message-id", required=True)
    analyze.add_argument("--idempotency-key", required=True)
    analyze.add_argument("--report-version", type=int, default=1)
    return parser


def _config(arguments: argparse.Namespace) -> LocalProfileConfig:
    return LocalProfileConfig(
        database_path=arguments.database_path,
        performance_csv_path=arguments.performance_csv_path,
        safe_trace_csv_path=arguments.safe_trace_csv_path,
        rules_dir=arguments.rules_dir,
        documents_dir=arguments.documents_dir,
        source_timezone=arguments.source_timezone,
    )


def _json_value(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", round_trip=True)
    return value


def _write_json(stream: TextIO, value: object) -> None:
    json.dump(
        _json_value(value),
        stream,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _require_independent_ids(**identifiers: str) -> None:
    normalized = {name: str(value).strip() for name, value in identifiers.items()}
    if any(not value for value in normalized.values()):
        raise ValueError("correlation identifiers must not be empty")
    if len(set(normalized.values())) != len(normalized):
        raise ValueError("correlation identifiers must remain independent")


async def _execute(arguments: argparse.Namespace) -> object:
    profile = LocalProfile.initialize(
        _config(arguments),
        reset=bool(getattr(arguments, "reset", False)),
    )
    if arguments.command == "init":
        summary = profile.database_summary
        return {
            "schema_version": summary.schema_version,
            "performance_rows": summary.performance_rows,
            "trace_rows": summary.trace_rows,
            "incident_rows": summary.incident_rows,
        }
    if arguments.command == "detect":
        _require_independent_ids(
            trace_id=arguments.trace_id,
            workflow_id=arguments.workflow_id,
        )
        triggers = await profile.detector.scan(
            arguments.trace_id,
            workflow_id=arguments.workflow_id,
        )
        return [item.to_data_part() for item in triggers]
    if arguments.command == "confirm":
        return await profile.detector.confirm(
            arguments.candidate_id,
            trace_id=arguments.trace_id,
            idempotency_key=arguments.idempotency_key,
            actor=arguments.actor,
            reason=arguments.reason,
        )
    if arguments.command in {"analyze", "rca"}:
        incident = await profile.incident_repository.get(arguments.incident_id)
        if incident is None:
            raise IncidentNotFoundError(arguments.incident_id)
        _require_independent_ids(
            message_id=arguments.message_id,
            workflow_id=arguments.workflow_id,
            incident_id=arguments.incident_id,
            trace_id=arguments.trace_id,
            idempotency_key=arguments.idempotency_key,
        )
        if arguments.trace_id != incident.trace_id:
            raise ValueError("trace_id must match the stored Incident snapshot")
        request = RcaRequest(
            message_id=arguments.message_id,
            workflow_id=arguments.workflow_id,
            incident_id=incident.incident_id,
            trace_id=arguments.trace_id,
            idempotency_key=arguments.idempotency_key,
            incident=incident,
            based_on_revision=incident.revision,
            requested_report_version=arguments.report_version,
        )
        return (await profile.rca_gateway.analyze(request)).to_data_part()
    raise ValueError("unsupported Local Profile command")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one command; successful stdout and failed stderr are JSON only."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        result = asyncio.run(_execute(arguments))
        assert_model_safe(result)
        _write_json(output, result)
        return 0
    except Exception as exc:
        message = " ".join(str(exc).split()) or "request failed"
        error: dict[str, Any] = {
            "error": type(exc).__name__,
            "message": message,
        }
        try:
            assert_model_safe(error)
        except Exception:
            error = {"error": type(exc).__name__, "message": "request rejected"}
        _write_json(errors, error)
        return 2


__all__ = ["main"]
