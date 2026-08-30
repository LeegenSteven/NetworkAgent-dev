#!/usr/bin/env python3
"""Safe, JSON-only local deployment entry point for NetworkAgent.

The module intentionally has no import-time dependency on project packages.  A
plain Python interpreter can therefore run ``doctor`` and receive a useful,
machine-readable dependency report before anything is installed.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import importlib
import json
import os
import platform
import shutil
import socket
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STACK_SCHEMA_VERSION = "1.0"
MARKER_NAME = ".local-stack.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8085
_PACKAGE_SOURCES = (
    REPOSITORY_ROOT / "packages" / "telco-domain" / "src",
    REPOSITORY_ROOT / "packages" / "telco-local" / "src",
    REPOSITORY_ROOT / "packages" / "telco-lab" / "src",
    REPOSITORY_ROOT / "networkagents" / "assurance" / "src",
)
_SOURCE_INPUTS = {
    "performance": REPOSITORY_ROOT
    / "data"
    / "samples"
    / "lte-demo"
    / "performance.csv",
    "safe_trace": REPOSITORY_ROOT
    / "data"
    / "samples"
    / "lte-demo"
    / "safe-cell-traces.csv",
    "rules": REPOSITORY_ROOT / "data" / "rca-rules" / "lte",
    "documents": REPOSITORY_ROOT / "data" / "docs" / "lte",
}


class SafeCliError(Exception):
    """An error whose stable code is safe to return without local details."""

    def __init__(self, code: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


_ERROR_MESSAGES = {
    "actions_disabled": "action approval is unavailable in disabled mode",
    "approval_binding_mismatch": "action approval does not match the reviewed preview",
    "approval_binding_required": "action approval requires the reviewed hash and revision",
    "approval_requires_incident": "action approval requires incident confirmation",
    "approval_requires_prior_preview": "action approval requires a prior preview command",
    "approval_reason_required": "action approval requires a non-empty reason",
    "dependencies_missing": "required local runtime dependencies are unavailable",
    "governance_unavailable": "the local governance engine is unavailable",
    "invalid_arguments": "command arguments are invalid",
    "no_candidates": "the sample data produced no incident candidates",
    "not_awaiting_approval": "the incident has no approvable simulated action",
    "port_unavailable": "the selected loopback port is unavailable",
    "runtime_failed": "the local operation failed safely",
    "server_dependencies_missing": "optional Assurance server dependencies are unavailable",
    "unsafe_workspace": "the selected workspace is not safe for local-stack operations",
    "workspace_not_initialized": "the selected workspace is not initialized",
    "workspace_not_owned": "the selected directory is not owned by local-stack",
}


def _write_json(stream: TextIO, value: object) -> None:
    json.dump(
        value,
        stream,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    """Recognize both POSIX links and Windows junction/reparse directories."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        raise SafeCliError("unsafe_workspace") from None


def _reject_non_local_path(path: str | os.PathLike[str]) -> None:
    """Reject network/device paths before any filesystem metadata operation."""

    rendered = os.fspath(path)
    if not isinstance(rendered, str) or not rendered or "\x00" in rendered:
        raise SafeCliError("unsafe_workspace")
    windows_form = rendered.replace("/", "\\")
    if windows_form.startswith("\\\\") or windows_form.startswith(
        ("\\\\?\\", "\\\\.\\")
    ):
        raise SafeCliError("unsafe_workspace")
    if os.name != "nt":
        if rendered.startswith("//"):
            raise SafeCliError("unsafe_workspace")
        return

    candidate = Path(rendered)
    absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
    drive = absolute.drive
    if not drive or drive.startswith("\\"):
        raise SafeCliError("unsafe_workspace")
    try:
        # DRIVE_FIXED=3. Mapped SMB drives (DRIVE_REMOTE=4), device roots,
        # removable media and unknown roots are outside this local-only profile.
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\"))
    except Exception:
        raise SafeCliError("unsafe_workspace") from None
    if drive_type != 3:
        raise SafeCliError("unsafe_workspace")


def _validate_workspace_path(path: Path) -> Path:
    _reject_non_local_path(path)
    supplied = Path(path).expanduser()
    _reject_non_local_path(supplied)
    if _is_link_like(supplied):
        raise SafeCliError("unsafe_workspace")
    resolved = supplied.resolve(strict=False)
    _reject_non_local_path(resolved)
    anchor = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    forbidden = {anchor, home, REPOSITORY_ROOT, *REPOSITORY_ROOT.parents}
    if any(_same_path(resolved, item) for item in forbidden):
        raise SafeCliError("unsafe_workspace")

    # Repository-contained workspaces are permitted only below the conventional
    # ignored .local area.  This makes a forged marker unable to turn source
    # directories into reset targets.
    local_root = (REPOSITORY_ROOT / ".local").resolve(strict=False)
    if _is_relative_to(resolved, REPOSITORY_ROOT) and not _is_relative_to(
        resolved, local_root
    ):
        raise SafeCliError("unsafe_workspace")
    return resolved


class Workspace:
    """One explicitly selected, marker-owned local workspace."""

    def __init__(self, root: Path) -> None:
        self.root = _validate_workspace_path(root)
        self.marker_path = self.root / MARKER_NAME
        self.state_dir = self.root / "state"
        self.artifacts_dir = self.root / "artifacts"
        self.database_path = self.state_dir / "networkagent.duckdb"

    def _validate_root(self, *, required: bool) -> None:
        if not self.root.exists():
            if required:
                raise SafeCliError("workspace_not_initialized", exit_code=1)
            return
        if _is_link_like(self.root) or not self.root.is_dir():
            raise SafeCliError("unsafe_workspace")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        if not _same_path(resolved, self.root):
            raise SafeCliError("unsafe_workspace")

    def _validate_owned_directory(self, target: Path) -> None:
        self._validate_root(required=True)
        if _is_link_like(target) or not target.is_dir():
            raise SafeCliError("unsafe_workspace")
        try:
            root = self.root.resolve(strict=True)
            resolved = target.resolve(strict=True)
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        if (
            not _same_path(target.parent, self.root)
            or not _same_path(resolved.parent, root)
            or resolved.name != target.name
        ):
            raise SafeCliError("unsafe_workspace")

    def _read_marker(self) -> dict[str, object]:
        self._validate_root(required=True)
        if not self.marker_path.is_file() or _is_link_like(self.marker_path):
            raise SafeCliError("workspace_not_initialized", exit_code=1)
        try:
            value = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SafeCliError("workspace_not_owned") from None
        if not isinstance(value, dict) or value.get("kind") != "networkagent-local-stack":
            raise SafeCliError("workspace_not_owned")
        if value.get("schema_version") != STACK_SCHEMA_VERSION:
            raise SafeCliError("workspace_not_owned")
        workspace_id = value.get("workspace_id")
        try:
            uuid.UUID(str(workspace_id))
        except (ValueError, TypeError, AttributeError):
            raise SafeCliError("workspace_not_owned") from None
        for target in (self.state_dir, self.artifacts_dir):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
        return value

    def marker(self) -> dict[str, object]:
        return self._read_marker()

    def prepare_init(self) -> tuple[str, bool, bool]:
        root_existed = self.root.exists()
        if root_existed:
            self._validate_root(required=True)
        if self.marker_path.exists():
            marker = self._read_marker()
            return str(marker["workspace_id"]), False, False
        if self.root.exists() and any(self.root.iterdir()):
            raise SafeCliError("workspace_not_owned")
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_root(required=True)
        self.state_dir.mkdir()
        self.artifacts_dir.mkdir()
        self._validate_owned_directory(self.state_dir)
        self._validate_owned_directory(self.artifacts_dir)
        return str(uuid.uuid4()), True, not root_existed

    def rollback_uncommitted_init(self, *, root_created: bool) -> None:
        """Restore the pre-init shape after a failed first initialization."""

        if self.marker_path.exists() or _is_link_like(self.marker_path):
            self._read_marker()
            return
        self._validate_root(required=True)
        for target in (self.state_dir, self.artifacts_dir):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
                shutil.rmtree(target)
        if root_created:
            try:
                self.root.rmdir()
            except OSError:
                # Never remove unexpected entries created outside this operation.
                pass

    def commit_marker(self, workspace_id: str) -> None:
        self._validate_owned_directory(self.state_dir)
        self._validate_owned_directory(self.artifacts_dir)
        marker = {
            "kind": "networkagent-local-stack",
            "schema_version": STACK_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "owned_entries": ["state", "artifacts", MARKER_NAME],
        }
        temporary = self.root / f"{MARKER_NAME}.tmp"
        if temporary.exists() or _is_link_like(temporary):
            raise SafeCliError("unsafe_workspace")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                handle.write(
                    json.dumps(
                        marker,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_root(required=True)
            temporary.replace(self.marker_path)
        except SafeCliError:
            raise
        except OSError:
            raise SafeCliError("unsafe_workspace") from None
        finally:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)

    def write_artifact(self, name: str, value: object) -> str:
        self._read_marker()
        if Path(name).name != name or not name:
            raise SafeCliError("unsafe_workspace")
        if not self.artifacts_dir.exists():
            try:
                self.artifacts_dir.mkdir()
            except OSError:
                raise SafeCliError("unsafe_workspace") from None
        self._validate_owned_directory(self.artifacts_dir)
        target = self.artifacts_dir / name
        temporary = self.artifacts_dir / f".{name}.tmp"
        if temporary.exists() or _is_link_like(temporary):
            raise SafeCliError("unsafe_workspace")
        try:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                handle.write(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_owned_directory(self.artifacts_dir)
            temporary.replace(target)
        except SafeCliError:
            raise
        except (OSError, TypeError, ValueError):
            raise SafeCliError("runtime_failed") from None
        finally:
            if temporary.exists() and not _is_link_like(temporary):
                temporary.unlink(missing_ok=True)
        return f"artifacts/{name}"

    def reset(self) -> dict[str, object]:
        self._read_marker()
        removed: list[str] = []
        for label, target in (("state", self.state_dir), ("artifacts", self.artifacts_dir)):
            if target.exists() or _is_link_like(target):
                self._validate_owned_directory(target)
                shutil.rmtree(target)
                removed.append(label)
        self.marker_path.unlink()
        removed.append("marker")
        workspace_removed = False
        try:
            self.root.rmdir()
            workspace_removed = True
        except OSError:
            # User-owned extra entries are deliberately preserved.
            pass
        return {
            "reset": True,
            "removed": removed,
            "workspace_removed": workspace_removed,
            "preserved_unknown_entries": not workspace_removed,
        }


def _configure_import_paths() -> None:
    for source in reversed(_PACKAGE_SOURCES):
        rendered = str(source)
        if rendered not in sys.path:
            sys.path.insert(0, rendered)


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _port_available(port: int) -> bool:
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        candidate.bind((DEFAULT_HOST, port))
        return True
    except OSError:
        return False
    finally:
        candidate.close()


def _model_value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _assert_project_safe(value: object) -> None:
    """Apply the canonical sensitive-data boundary before durable/output JSON."""

    _configure_import_paths()
    try:
        from telco_domain import assert_model_safe
    except Exception:
        # doctor and dependency errors must remain usable before installation;
        # those payloads are fixed whitelists and contain no caller values.
        return
    try:
        assert_model_safe(value)
    except Exception:
        raise SafeCliError("runtime_failed") from None


def _safe_action_preview(action: object) -> dict[str, object]:
    resources = _model_value(action, "target_resources", ())
    if resources is None:
        resources = ()
    safe_resources = []
    for resource in resources:
        technology = _model_value(resource, "technology")
        safe_resources.append(
            {
                "resource_id": str(_model_value(resource, "resource_id", "")),
                "resource_type": str(
                    _enum_value(_model_value(resource, "resource_type", ""))
                ),
                "technology": (
                    None if technology is None else str(_enum_value(technology))
                ),
            }
        )
    return {
        "action_hash": str(_model_value(action, "action_hash", "")),
        "action_type": str(
            _enum_value(
                _model_value(action, "action_type", _model_value(action, "kind", "SIMULATE"))
            )
        ),
        "resources": safe_resources,
        "risk": str(
            _enum_value(_model_value(action, "risk_level", "LOCAL_SIMULATION"))
        ),
    }


def _incident_state(result: object) -> str:
    incident = _model_value(result, "incident")
    status = _model_value(incident, "status", "UNKNOWN")
    return str(_enum_value(status))


class LocalStackRuntime:
    """Lazy adapter over the existing Local Profile and governance engine."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        _configure_import_paths()

    def _config(self) -> object:
        try:
            from telco_local import LocalProfileConfig
        except Exception:
            raise SafeCliError("dependencies_missing") from None
        return LocalProfileConfig(
            database_path=self.workspace.database_path,
            performance_csv_path=_SOURCE_INPUTS["performance"],
            safe_trace_csv_path=_SOURCE_INPUTS["safe_trace"],
            rules_dir=_SOURCE_INPUTS["rules"],
            documents_dir=_SOURCE_INPUTS["documents"],
            source_timezone="UTC",
        )

    def _assurance_config(self, port: int) -> object:
        try:
            from telco_assurance_agent.config import AssuranceConfig
        except Exception:
            raise SafeCliError("server_dependencies_missing") from None
        return AssuranceConfig(
            database_path=self.workspace.database_path,
            performance_csv_path=_SOURCE_INPUTS["performance"],
            safe_trace_csv_path=_SOURCE_INPUTS["safe_trace"],
            rules_dir=_SOURCE_INPUTS["rules"],
            documents_dir=_SOURCE_INPUTS["documents"],
            public_url=f"http://{DEFAULT_HOST}:{port}/",
            actor="local-stack-assurance",
            host=DEFAULT_HOST,
            port=port,
        )

    def doctor(self, *, port: int) -> dict[str, object]:
        version = sys.version_info
        python_supported = (3, 12) <= version[:2] < (3, 14)
        core_modules = ("duckdb", "pydantic", "telco_domain", "telco_local")
        core = all(_can_import(name) for name in core_modules)
        governance = _can_import("telco_local.governance")
        server = all(
            _can_import(name)
            for name in ("a2a", "starlette", "uvicorn", "telco_assurance_agent")
        )
        data_checks = {
            name: path.is_dir() if name in {"rules", "documents"} else path.is_file()
            for name, path in _SOURCE_INPUTS.items()
        }
        data_ready = all(data_checks.values())
        available = _port_available(port)
        demo_ready = python_supported and core and governance and data_ready
        return {
            "ready": demo_ready,
            "demo_ready": demo_ready,
            "server_ready": demo_ready and server and available,
            "python": {
                "supported": python_supported,
                "version": f"{version.major}.{version.minor}.{version.micro}",
            },
            "dependencies": {
                "core": core,
                "governance": governance,
                "server": server,
            },
            "data": {"ready": data_ready, "checks": data_checks},
            "port": {"number": port, "available": available},
            "network": {"bind_host": DEFAULT_HOST, "external_access": False},
        }

    def initialize(self) -> dict[str, object]:
        doctor = self.doctor(port=DEFAULT_PORT)
        if not doctor["python"]["supported"] or not doctor["dependencies"]["core"]:
            raise SafeCliError("dependencies_missing")
        if not doctor["data"]["ready"]:
            raise SafeCliError("runtime_failed")
        try:
            from telco_local import LocalProfile

            profile = LocalProfile.initialize(self._config(), reset=False)
            server_schema = False
            if doctor["dependencies"]["server"]:
                from telco_assurance_agent.app import initialize_assurance

                initialize_assurance(self._assurance_config(DEFAULT_PORT), reset=False)
                server_schema = True
            summary = profile.database_summary
            return {
                "schema_version": summary.schema_version,
                "performance_rows": summary.performance_rows,
                "trace_rows": summary.trace_rows,
                "incident_rows": summary.incident_rows,
                "server_schema": server_schema,
            }
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    def status(self, *, port: int) -> dict[str, object]:
        self.workspace.marker()
        try:
            from telco_local import LocalProfile

            profile = LocalProfile.open_existing(self._config())
            summary = profile.database_summary
        except Exception:
            return {
                "ready": False,
                "database": {"initialized": False},
                "server": {
                    "host": DEFAULT_HOST,
                    "port": port,
                    "available": _port_available(port),
                },
            }
        doctor = self.doctor(port=port)
        return {
            "ready": bool(doctor["demo_ready"]),
            "database": {
                "initialized": True,
                "schema_version": summary.schema_version,
                "performance_rows": summary.performance_rows,
                "trace_rows": summary.trace_rows,
                "incident_rows": summary.incident_rows,
            },
            "runtime": {
                "demo_ready": doctor["demo_ready"],
                "server_dependencies": doctor["dependencies"]["server"],
                "governance": doctor["dependencies"]["governance"],
            },
            "server": {
                "host": DEFAULT_HOST,
                "port": port,
                "available": doctor["port"]["available"],
                "external_access": False,
            },
        }

    async def _run_demo(
        self,
        *,
        action_mode: str,
        confirm_incident: bool,
        approve_action: bool,
        reason: str | None,
        expected_action_hash: str | None,
        expected_revision: int | None,
        verification_outcome: str,
    ) -> dict[str, object]:
        try:
            from telco_local import LocalProfile
        except Exception:
            raise SafeCliError("dependencies_missing") from None

        profile = LocalProfile.open_existing(self._config())
        triggers = await profile.detector.scan(
            "local-stack-detect-trace-v1",
            workflow_id="local-stack-detect-workflow-v1",
        )
        if not triggers:
            raise SafeCliError("no_candidates")
        selected = sorted(triggers, key=lambda item: item.incident_id)[0]
        selected_incident = selected.incident
        candidate = {
            "incident_id": selected.incident_id,
            "severity": str(_enum_value(selected_incident.severity)),
            "technology": str(_enum_value(selected_incident.technology)),
            "resource_count": len(selected_incident.affected_resources),
        }
        base: dict[str, object] = {
            "workflow_id": "local-governance-demo-v1",
            "action_mode": action_mode,
            "candidate_count": len(triggers),
            "selected_candidate": candidate,
            "state": "PREVIEW",
            "closed_loop": False,
            "approval": {"incident_confirmed": False, "action_approved": False},
        }
        if not confirm_incident and not approve_action:
            return base

        digest = hashlib.sha256(selected.incident_id.encode("utf-8")).hexdigest()[:16]
        if confirm_incident:
            incident = await profile.detector.confirm(
                selected.incident_id,
                trace_id=f"local-stack-confirm-trace-{digest}",
                idempotency_key=f"local-stack-confirm-key-{digest}",
                actor="local-stack-operator",
                reason="explicit local demo incident confirmation",
            )
        else:
            incident = await profile.incident_repository.get(selected.incident_id)
            if incident is None:
                raise SafeCliError("approval_requires_incident")
        base["approval"] = {"incident_confirmed": True, "action_approved": False}

        try:
            from telco_local.governance import LocalGovernanceEngine
        except Exception:
            raise SafeCliError("governance_unavailable") from None
        engine = LocalGovernanceEngine(
            profile.incident_repository,
            profile.rca_gateway,
            clock=lambda: datetime.now(UTC),
        )
        prepared = await engine.prepare(
            incident.incident_id,
            idempotency_key=f"local-stack-prepare-key-{digest}",
            actor="local-governance",
        )
        base["state"] = _incident_state(prepared)
        action = _model_value(prepared, "action")
        awaiting_approval = bool(_model_value(prepared, "awaiting_approval", False))
        if action is not None:
            base["action_preview"] = _safe_action_preview(action)
            if awaiting_approval:
                base["action_preview"]["expected_revision"] = int(
                    _model_value(_model_value(prepared, "incident"), "revision")
                )
        if action is None:
            base["outcome"] = "NO_ACTION_PROPOSED"
            return base
        if not approve_action:
            if not awaiting_approval:
                base["outcome"] = "GOVERNANCE_RESUME_REQUIRES_ORIGINAL_BINDING"
                return base
            base["outcome"] = "AWAITING_EXPLICIT_APPROVAL"
            return base
        if action_mode == "disabled":
            raise SafeCliError("actions_disabled")
        normalized_reason = " ".join((reason or "").split())
        if not normalized_reason:
            raise SafeCliError("approval_reason_required")
        current_action_hash = str(_model_value(action, "action_hash", ""))
        current_revision = int(
            _model_value(_model_value(prepared, "incident"), "revision")
        )
        if expected_action_hash != current_action_hash or (
            awaiting_approval and expected_revision != current_revision
        ):
            raise SafeCliError("approval_binding_mismatch")

        decided = await engine.decide(
            incident.incident_id,
            approve=True,
            actor="local-stack-operator",
            reason=normalized_reason,
            idempotency_key=f"local-stack-decision-key-{digest}",
            expected_action_hash=expected_action_hash,
            expected_revision=expected_revision,
        )
        decided_state = _incident_state(decided)
        if decided_state == "REJECTED":
            base.update(
                {
                    "state": decided_state,
                    "closed_loop": False,
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": False,
                        "decision_state": decided_state,
                    },
                    "outcome": "APPROVAL_NOT_EFFECTIVE",
                }
            )
            return base
        executed = await engine.execute(
            incident.incident_id,
            idempotency_key=f"local-stack-execute-key-{digest}",
            actor="local-simulator",
            verification_passed=verification_outcome == "passed",
        )
        executed_state = _incident_state(executed)
        if executed_state == "FAILED":
            base.update(
                {
                    "state": executed_state,
                    "closed_loop": False,
                    "approval": {
                        "incident_confirmed": True,
                        "action_approved": False,
                        "decision_state": decided_state,
                    },
                    "outcome": "APPROVAL_NOT_EFFECTIVE",
                }
            )
            return base
        base.update(
            {
                "state": executed_state,
                "closed_loop": executed_state == "RESOLVED",
                "approval": {
                    "incident_confirmed": True,
                    "action_approved": True,
                    "decision_state": decided_state,
                },
                "outcome": (
                    "SIMULATED_AND_VERIFIED"
                    if verification_outcome == "passed"
                    else "SIMULATED_AND_REOPENED"
                ),
            }
        )
        return base

    def demo(
        self,
        *,
        action_mode: str,
        confirm_incident: bool,
        approve_action: bool,
        reason: str | None,
        expected_action_hash: str | None,
        expected_revision: int | None,
        verification_outcome: str,
    ) -> dict[str, object]:
        if approve_action and confirm_incident:
            raise SafeCliError("approval_requires_prior_preview")
        if approve_action and action_mode != "simulate":
            raise SafeCliError("actions_disabled")
        if approve_action and (
            not expected_action_hash or expected_revision is None
        ):
            raise SafeCliError("approval_binding_required")
        if not approve_action and (
            expected_action_hash is not None or expected_revision is not None
        ):
            raise SafeCliError("invalid_arguments")
        try:
            result = asyncio.run(
                self._run_demo(
                    action_mode=action_mode,
                    confirm_incident=confirm_incident,
                    approve_action=approve_action,
                    reason=reason,
                    expected_action_hash=expected_action_hash,
                    expected_revision=expected_revision,
                    verification_outcome=verification_outcome,
                )
            )
            _assert_project_safe(result)
            artifact = self.workspace.write_artifact("demo-result.json", result)
            result["artifacts"] = [artifact]
            return result
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None

    def serve(self, *, port: int) -> None:
        if not _port_available(port):
            raise SafeCliError("port_unavailable")
        try:
            import uvicorn
            from telco_assurance_agent.app import create_app
            from telco_assurance_agent.transport_http import BoundedH11Protocol
        except Exception:
            raise SafeCliError("server_dependencies_missing") from None
        status = self.status(port=port)
        if not status["database"]["initialized"]:
            raise SafeCliError("workspace_not_initialized", exit_code=1)
        try:
            application = create_app(self._assurance_config(port))
            uvicorn.run(
                application,
                host=DEFAULT_HOST,
                port=port,
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
        except SafeCliError:
            raise
        except Exception:
            raise SafeCliError("runtime_failed") from None


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise SafeCliError("invalid_arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="networkagent-local-stack",
        description="Safe local NetworkAgent deployment and governance demo",
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--action-mode", choices=("disabled", "simulate"), default="disabled"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check the local runtime without writes")
    commands.add_parser("init", help="initialize an explicitly selected workspace")
    commands.add_parser("status", help="inspect workspace and runtime readiness")
    demo = commands.add_parser("demo", help="run a deterministic governance demo")
    demo.add_argument("--confirm-incident", action="store_true")
    demo.add_argument("--approve-action", action="store_true")
    demo.add_argument("--reason")
    demo.add_argument("--expected-action-hash")
    demo.add_argument("--expected-revision", type=int)
    demo.add_argument(
        "--verification-outcome", choices=("passed", "failed"), default="passed"
    )
    commands.add_parser("serve", help="run the optional loopback A2A service in foreground")
    reset = commands.add_parser("reset", help="reset only marker-owned local state")
    reset.add_argument("--yes", action="store_true")
    return parser


def _workspace_payload(workspace_id: str, *, initialized: bool) -> dict[str, object]:
    return {"workspace_id": workspace_id, "initialized": initialized}


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    runtime_factory: Callable[[Workspace], Any] = LocalStackRuntime,
) -> int:
    """Run one command; stdout/stderr are always single JSON documents."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        if not 1 <= arguments.port <= 65_535:
            raise SafeCliError("invalid_arguments")
        workspace = Workspace(arguments.workspace)
        runtime = runtime_factory(workspace)

        if arguments.command == "doctor":
            report = runtime.doctor(port=arguments.port)
            _write_json(output, {"ok": bool(report["ready"]), "command": "doctor", "report": report})
            return 0 if report["ready"] else 1
        if arguments.command == "init":
            workspace_id, created, root_created = workspace.prepare_init()
            try:
                result = runtime.initialize()
                workspace.commit_marker(workspace_id)
            except BaseException:
                if created:
                    workspace.rollback_uncommitted_init(root_created=root_created)
                raise
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "init",
                    "workspace": _workspace_payload(workspace_id, initialized=True),
                    "created": created,
                    "database": result,
                    "network": {"external_access": False},
                    "action_mode": arguments.action_mode,
                },
            )
            return 0
        if arguments.command == "status":
            marker = workspace.marker()
            report = runtime.status(port=arguments.port)
            payload = {
                "ok": bool(report["ready"]),
                "command": "status",
                "workspace": _workspace_payload(str(marker["workspace_id"]), initialized=True),
                "report": report,
                "action_mode": arguments.action_mode,
            }
            _write_json(output if report["ready"] else errors, payload)
            return 0 if report["ready"] else 1
        if arguments.command == "demo":
            marker = workspace.marker()
            if arguments.approve_action and arguments.confirm_incident:
                raise SafeCliError("approval_requires_prior_preview")
            if arguments.approve_action and arguments.action_mode != "simulate":
                raise SafeCliError("actions_disabled")
            if arguments.approve_action and (
                not arguments.expected_action_hash
                or arguments.expected_revision is None
            ):
                raise SafeCliError("approval_binding_required")
            if arguments.verification_outcome != "passed" and (
                arguments.action_mode != "simulate" or not arguments.approve_action
            ):
                raise SafeCliError("actions_disabled")
            result = runtime.demo(
                action_mode=arguments.action_mode,
                confirm_incident=arguments.confirm_incident,
                approve_action=arguments.approve_action,
                reason=arguments.reason,
                expected_action_hash=arguments.expected_action_hash,
                expected_revision=arguments.expected_revision,
                verification_outcome=arguments.verification_outcome,
            )
            _assert_project_safe(result)
            _write_json(
                output,
                {
                    "ok": True,
                    "command": "demo",
                    "workspace": _workspace_payload(str(marker["workspace_id"]), initialized=True),
                    "result": result,
                },
            )
            return 0
        if arguments.command == "serve":
            workspace.marker()
            if arguments.action_mode != "disabled":
                raise SafeCliError("actions_disabled")
            # This is intentionally foreground-only; no PID files or orphaned
            # background processes are created by local-stack.
            runtime.serve(port=arguments.port)
            return 0
        if arguments.command == "reset":
            marker = workspace.marker()
            if not arguments.yes:
                _write_json(
                    output,
                    {
                        "ok": False,
                        "command": "reset",
                        "confirmation_required": True,
                        "workspace": _workspace_payload(
                            str(marker["workspace_id"]), initialized=True
                        ),
                    },
                )
                return 1
            result = workspace.reset()
            _write_json(output, {"ok": True, "command": "reset", **result})
            return 0
        raise SafeCliError("invalid_arguments")
    except SafeCliError as exc:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": _ERROR_MESSAGES.get(exc.code, "request rejected"),
                },
            },
        )
        return exc.exit_code
    except Exception:
        _write_json(
            errors,
            {
                "ok": False,
                "error": {
                    "code": "runtime_failed",
                    "message": _ERROR_MESSAGES["runtime_failed"],
                },
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
