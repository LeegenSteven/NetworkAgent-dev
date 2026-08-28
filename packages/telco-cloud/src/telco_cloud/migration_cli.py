"""Explicit CLI for one-time Canonical Incident migration bundles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .migration import (
    MAX_MIGRATION_BUNDLE_BYTES,
    MigrationBundleError,
    MigrationDependencyError,
    dump_migration_bundle,
    export_migration_bundle,
    import_migration_bundle,
    load_migration_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telco-cloud-migrate",
        description=(
            "Create, validate, and replay one-time Canonical Incident migration "
            "bundles. This command never enables dual writes."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser(
        "export-duckdb",
        help="export an initialized Local Profile canonical repository",
    )
    export.add_argument("--database", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--source-profile", required=True)
    export.add_argument("--overwrite", action="store_true")

    validate = commands.add_parser(
        "validate",
        help="validate checksum, schema, privacy, depth, and capacity offline",
    )
    validate.add_argument("--input", required=True, type=Path)

    import_parser = commands.add_parser(
        "import-spanner",
        help="import eligible DETECTED/revision-0 entries into Canonical v2",
    )
    import_parser.add_argument("--input", required=True, type=Path)
    import_parser.add_argument(
        "--offline-plan",
        action="store_true",
        help=(
            "validate and classify only the bundle; do not connect to or "
            "preflight the target database"
        ),
    )
    return parser


def _safe_output(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(
        json.dumps(
            dict(payload),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _load(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_MIGRATION_BUNDLE_BYTES:
            raise MigrationBundleError("migration bundle exceeds 16 MiB")
        with path.open("rb") as stream:
            data = stream.read(MAX_MIGRATION_BUNDLE_BYTES + 1)
        if len(data) > MAX_MIGRATION_BUNDLE_BYTES:
            raise MigrationBundleError("migration bundle exceeds 16 MiB")
        return data
    except MigrationBundleError:
        raise
    except OSError:
        raise MigrationBundleError("migration bundle could not be read") from None


def _write(path: Path, data: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise MigrationBundleError("migration output already exists")
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
            try:
                temporary_path.unlink()
            except OSError:
                # The destination is already an atomic hard-link publication;
                # a stale private temp name is cleanup-only, not a failed export.
                pass
        temporary_path = None
    except FileExistsError:
        raise MigrationBundleError("migration output already exists") from None
    except OSError:
        raise MigrationBundleError("migration bundle could not be written") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    source_repository: Any | None = None,
    target_repository: Any | None = None,
    clock: Any | None = None,
) -> int:
    """Run one explicit migration command; ``--help`` performs no I/O."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    arguments = _parser().parse_args(argv)
    source = os.environ if environ is None else environ
    trusted_clock = clock or (lambda: datetime.now(UTC))

    try:
        if arguments.command == "export-duckdb":
            repository = source_repository
            if repository is None:
                # Optional local dependency stays outside import/help paths.
                try:
                    from telco_local import DuckDbIncidentRepository
                except ImportError:
                    raise MigrationDependencyError(
                        "migration source dependency is unavailable"
                    ) from None
                try:
                    repository = DuckDbIncidentRepository(
                        arguments.database,
                        ensure_schema=False,
                    )
                except FileNotFoundError:
                    raise MigrationBundleError(
                        "migration source is not initialized"
                    ) from None
                except (RuntimeError, ValueError):
                    raise MigrationBundleError(
                        "migration source schema is invalid"
                    ) from None
                except Exception:
                    raise MigrationDependencyError(
                        "migration source dependency failed"
                    ) from None
            try:
                bundle = asyncio.run(
                    export_migration_bundle(
                        repository,
                        source_profile=arguments.source_profile,
                        exported_at=trusted_clock(),
                    )
                )
            except MigrationBundleError:
                raise
            except Exception:
                raise MigrationDependencyError(
                    "migration source dependency failed"
                ) from None
            _write(
                arguments.output,
                dump_migration_bundle(bundle),
                overwrite=arguments.overwrite,
            )
            _safe_output(
                output,
                {
                    "status": "EXPORTED",
                    "entries": len(bundle.entries),
                    "checksum_sha256": bundle.checksum_sha256,
                },
            )
            return 0

        bundle = load_migration_bundle(_load(arguments.input))
        if arguments.command == "validate":
            report = asyncio.run(
                import_migration_bundle(bundle, None, dry_run=True)
            )
            _safe_output(
                output,
                {"status": "VALID", **report.model_dump(mode="json")},
            )
            return 0

        if arguments.command == "import-spanner":
            repository = target_repository
            if not arguments.offline_plan and repository is None:
                from .config import CloudProfileConfig, compose_spanner_database
                from .incident_repository import SpannerIncidentRepository
                from .schema import MIGRATION_DATABASE_ROLE

                profile = CloudProfileConfig.from_env(source)
                if profile.database_role != MIGRATION_DATABASE_ROLE:
                    raise MigrationBundleError(
                        "live migration requires the exact migration-importer role"
                    )
                try:
                    database = compose_spanner_database(profile)
                except ValueError:
                    raise
                except Exception:
                    raise MigrationDependencyError(
                        "migration target dependency failed"
                    ) from None
                repository = SpannerIncidentRepository(database)
            report = asyncio.run(
                import_migration_bundle(
                    bundle,
                    repository,
                    dry_run=arguments.offline_plan,
                )
            )
            _safe_output(
                output,
                {"status": "OFFLINE_PLAN" if arguments.offline_plan else "IMPORTED",
                 **report.model_dump(mode="json")},
            )
            return 0 if report.quarantined == 0 else 3

        raise AssertionError(f"unknown migration command {arguments.command!r}")
    except MigrationDependencyError:
        _safe_output(errors, {"error": "MIGRATION_DEPENDENCY_UNAVAILABLE"})
        return 4
    except (FileNotFoundError, MigrationBundleError, RuntimeError, ValueError):
        _safe_output(errors, {"error": "MIGRATION_INVALID"})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
