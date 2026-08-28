"""Explicit administrative entry point for the canonical Spanner schema."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from typing import Any

from .config import CloudSchemaAdminConfig, compose_spanner_admin_database
from .schema import apply_object_schema, apply_schema


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telco-cloud-schema",
        description=(
            "Explicitly manage the Canonical v2 Spanner schema. Runtime "
            "services never apply DDL automatically."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "apply",
        help="apply canonical objects and reconcile runtime database roles",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    client: Any | None = None,
) -> int:
    """Run one explicit schema command; parsing help performs no cloud I/O."""

    arguments = _parser().parse_args(argv)
    if arguments.command == "apply":
        profile = CloudSchemaAdminConfig.from_env(environ)
        database = compose_spanner_admin_database(profile, client=client)
        if profile.emulator_host is None:
            apply_schema(database)
        else:
            # Cloud Spanner Emulator intentionally has no IAM/FGAC support.
            # Its explicit loopback profile validates object DDL only; a
            # production profile can never enter this reduced branch.
            apply_object_schema(database)
        return 0
    raise AssertionError(f"unknown schema command {arguments.command!r}")


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())


__all__ = ["main"]
