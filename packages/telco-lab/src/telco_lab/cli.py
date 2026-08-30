"""JSON-only command line interface for audited local dataset workspaces."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import pydantic
from telco_domain import SCHEMA_VERSION as TELCO_DOMAIN_SCHEMA_VERSION

from .catalog import CatalogProvider, PackageCatalogProvider
from .downloader import SecureDownloader
from .errors import LabError
from .pipeline import (
    BUBBLERAN_PIPELINE_ID,
    evaluate_cached_bubbleran,
    fetch_and_evaluate_bubbleran,
)
from .workspace import TelcoLab
from .version import PACKAGE_VERSION


class _HelpRequested(Exception):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__("help requested")


class _JsonHelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs):  # noqa: ANN001
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=0,
            default=argparse.SUPPRESS,
            **kwargs,
        )

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        raise _HelpRequested(parser.format_help())


class _SafeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument(
            "-h",
            "--help",
            action=_JsonHelpAction,
            help="show this help message as JSON and exit",
        )

    def error(self, _message: str) -> None:
        raise LabError("invalid_arguments")

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status == 0:
            raise _HelpRequested(self.format_help())
        raise LabError("invalid_arguments")


CommandHandler = Callable[[argparse.Namespace, TelcoLab], tuple[object, int]]
CommandInstaller = Callable[[argparse._SubParsersAction], None]


def _parser(*, extensions: Sequence[CommandInstaller] = ()) -> argparse.ArgumentParser:
    """Build the parser; extensions can install future prepare/evaluate/demo commands."""

    parser = _SafeArgumentParser(
        prog="telco-lab",
        description="Secure, reproducible local telecom dataset laboratory",
    )
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog", help="list the audited package catalog offline")

    fetch = commands.add_parser("fetch", help="download one explicitly selected resource")
    fetch.add_argument("resource_id")
    fetch.add_argument("--accept-license", required=True)

    verify = commands.add_parser("verify", help="verify locked local artifacts")
    verify.add_argument("resource_id", nargs="?")

    evaluate = commands.add_parser(
        "evaluate",
        help="evaluate an already verified dataset without network access",
    )
    evaluate.add_argument("pipeline_id", choices=(BUBBLERAN_PIPELINE_ID,))
    evaluate.add_argument("--overlap-threshold", type=float, default=0.1)

    run = commands.add_parser(
        "run",
        help="fetch a pinned dataset and run its reproducible local evaluation",
    )
    run.add_argument("pipeline_id", choices=(BUBBLERAN_PIPELINE_ID,))
    run.add_argument("--accept-license", required=True)
    run.add_argument("--overlap-threshold", type=float, default=0.1)
    for install in extensions:
        install(commands)
    return parser


def _write_json(stream: TextIO, value: object) -> None:
    json.dump(
        value,
        stream,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _catalog_payload(lab: TelcoLab) -> dict[str, object]:
    catalog = lab.catalog()
    return {
        "schema_version": catalog.schema_version,
        "catalog_id": catalog.catalog_id,
        "catalog_version": catalog.catalog_version,
        "resources": [
            {
                "resource_id": item.resource_id,
                "dataset_id": item.dataset_id,
                "dataset_version": item.dataset_version,
                "filename": item.filename,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "media_type": item.media_type,
                "adapter": item.adapter,
                "license": {
                    "id": item.license.id,
                    "name": item.license.name,
                    "attribution": item.license.attribution,
                    "evidence_sha256": item.license.evidence_sha256,
                    "reviewed_at": item.license.reviewed_at.isoformat(),
                    "acceptance_required": item.license.acceptance_required,
                },
            }
            for item in catalog.resources
        ],
    }


def _execution_payload() -> dict[str, object]:
    return {
        "report_schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": f"package:{PACKAGE_VERSION}",
        "runtime": {
            "python": platform.python_version(),
            "pydantic": pydantic.__version__,
            "telco_domain_schema": TELCO_DOMAIN_SCHEMA_VERSION,
        },
    }


def _dispatch(
    arguments: argparse.Namespace,
    lab: TelcoLab,
    *,
    extension_handlers: Mapping[str, CommandHandler],
) -> tuple[object, int]:
    if arguments.command == "catalog":
        return {"ok": True, "catalog": _catalog_payload(lab)}, 0
    if arguments.command == "fetch":
        artifact = lab.fetch(
            arguments.resource_id,
            accepted_license=arguments.accept_license,
        )
        return {
            "ok": True,
            "artifact": {
                "resource_id": artifact.resource_id,
                "filename": artifact.filename,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "cached": artifact.cached,
            },
        }, 0
    if arguments.command == "verify":
        report = lab.verify(arguments.resource_id)
        return {
            "ok": report.valid,
            "verification": {
                "schema_version": report.schema_version,
                "catalog_id": report.catalog_id,
                "catalog_version": report.catalog_version,
                "valid": report.valid,
                "artifacts": [
                    {
                        "resource_id": item.resource_id,
                        "filename": item.filename,
                        "status": item.status,
                    }
                    for item in report.artifacts
                ],
            },
        }, 0 if report.valid else 1
    if arguments.command == "evaluate":
        result = evaluate_cached_bubbleran(
            lab,
            overlap_threshold=arguments.overlap_threshold,
        )
        return {
            "ok": True,
            "mode": "offline-cache",
            "execution": _execution_payload(),
            "result": result.summary(),
        }, 0
    if arguments.command == "run":
        result = fetch_and_evaluate_bubbleran(
            lab,
            accepted_license=arguments.accept_license,
            overlap_threshold=arguments.overlap_threshold,
        )
        return {
            "ok": True,
            "mode": "fetch-and-evaluate",
            "execution": _execution_payload(),
            "result": result.summary(),
        }, 0
    handler = extension_handlers.get(arguments.command)
    if handler is not None:
        return handler(arguments, lab)
    raise LabError("invalid_arguments")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    provider: CatalogProvider | None = None,
    downloader: SecureDownloader | None = None,
    command_installers: Sequence[CommandInstaller] = (),
    command_handlers: Mapping[str, CommandHandler] | None = None,
) -> int:
    """Execute a command; output is JSON and errors never contain input values."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser(extensions=command_installers).parse_args(
            list(argv) if argv is not None else None
        )
        lab = TelcoLab(
            provider if provider is not None else PackageCatalogProvider(),
            arguments.workspace,
            downloader=downloader,
        )
        payload, exit_code = _dispatch(
            arguments,
            lab,
            extension_handlers=command_handlers or {},
        )
        _write_json(output, payload)
        return exit_code
    except _HelpRequested as help_request:
        _write_json(
            output,
            {
                "ok": True,
                "help": {"program": "telco-lab", "text": help_request.text},
            },
        )
        return 0
    except LabError as exc:
        _write_json(
            errors,
            {"ok": False, "error": {"code": exc.code, "message": str(exc)}},
        )
        return 2
    except Exception:
        error = LabError("internal_error")
        _write_json(
            errors,
            {"ok": False, "error": {"code": error.code, "message": str(error)}},
        )
        return 2


__all__ = ["CommandHandler", "CommandInstaller", "main"]
