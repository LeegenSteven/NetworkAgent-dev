"""Build the fixed, offline, commit-bound local submission bundle."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Sequence, TextIO


RESULT_SCHEMA = "networkagent-local-submission-build-result/1.0"
ERROR_SCHEMA = "networkagent-local-submission-error/1.0"
LEDGER_SCHEMA = "networkagent-local-submission-evidence/1.0"
INDEX_SCHEMA = "networkagent-local-submission-index/1.0"
LIMITATIONS_SCHEMA = "networkagent-local-submission-limitations/1.0"
MANIFEST_SCHEMA = "networkagent-local-submission-manifest/1.0"
REPOSITORY = "LeegenSteven/NetworkAgent-dev"
EVIDENCE_AS_OF = "2026-08-31T12:04:19Z"
EVIDENCE_CUTOFF_COMMIT = "aa50d74034d77bb36fbf2fad60d58e449308a1f1"
EXPECTED_LEDGER_SHA256 = (
    "b93e1ed2fa6f8ae890c9e76d5c5d0fa3fb797d2c9ea94990c2e3c6cc175acbeb"
)
EXPECTED_INDEX_SHA256 = (
    "741f4cfb29c992e88eaaba0887cdc11373ae536f1683bc86280e6ce5d3594260"
)
EXPECTED_LIMITATIONS_SHA256 = (
    "dd0ca40daeca8d004dafddfee673403f9750bb67a781528721cddd5ec39403f7"
)
OUTPUT_DIRECTORY = Path(".local/networkagent-submission")
OUTPUT_FILENAMES = (
    "submission-index.json",
    "limitations.json",
    "REPRODUCE.md",
    "index.html",
    "manifest.json",
)
EVIDENCE_LEDGER = Path("docs/evidence/local-submission-evidence.v1.json")
SLICE_IDS = (
    "S4-01",
    "S4-02",
    "S4-03",
    "S4-04",
    "S4-05",
    "S7-01",
    "S7-02",
    "S7-03",
    "S7-04",
)
EXPECTED_RC_SHA = {
    "S4-01": "cb4a4e7191f67aa71ef980668352d55001e23142",
    "S4-02": "69643e8a6f79b1264d60e5517eeb9a24035c8e7d",
    "S4-03": "faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c",
    "S4-04": "54551feb43be60c3b9bdd5eab076cdb7c0aba61a",
    "S4-05": "2e59d7ca88cc550e315d63e80339909ef619cd2c",
    "S7-01": "c08d634c9c3deb628df5f98d4f60dd1675cd5706",
    "S7-02": "79feeee6771749bbdd1ce7ce44b77193a1db544f",
    "S7-03": "46318cbf84b65c3060358dffb49b829479803308",
    "S7-04": "b8a9e958a0a3354634f87e2fbc8f76aaf60913dd",
}
EXPECTED_EVIDENCE_CLASSIFICATION = {
    "S4-01": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "ABSENT_BY_SCHEMA",
        "value": None,
    },
    "S4-02": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_CANONICAL_LIFECYCLE_EVIDENCE",
    },
    "S4-03": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_DEMO_ACCEPTANCE_SLO_EVIDENCE",
    },
    "S4-04": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_COLD_BACKUP_RECOVERY_EVIDENCE",
    },
    "S4-05": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE",
    },
    "S7-01": {
        "scope": "CI_RUNTIME_OUTPUT",
        "state": "VERIFIED",
        "value": "LOCAL_NATIVE_SIMULATION_EVIDENCE",
    },
    "S7-02": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_NATIVE_SIMULATION_EVIDENCE",
    },
    "S7-03": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE",
    },
    "S7-04": {
        "scope": "PRIMARY_SUMMARY_PAYLOAD",
        "state": "VERIFIED",
        "value": "PINNED_UPSTREAM_RCAEVAL_RE2OB_SLICE",
    },
}
EXPECTED_STAGE_UPDATES = {
    "S7-03": [
        {
            "effect": "FIXTURE_ENTRY_ADDED_STAGE_REMAINS_IN_PROGRESS",
            "id": "P3e-5",
        }
    ]
}

_LEDGER_MAX_BYTES = 512 * 1024
_OUTPUT_FILE_MAX_BYTES = 512 * 1024
_OUTPUT_TOTAL_MAX_BYTES = 2 * 1024 * 1024
_LOCK_BYTES = b"networkagent-local-submission-lock/1\n"
_MAX_JSON_NODES = 20_000
_MAX_STRING_BYTES = 16 * 1024
_MAX_INTEGER = (1 << 63) - 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DECIMAL_ID = re.compile(r"[1-9][0-9]*\Z")
_TOKEN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_WORKFLOWS = {
    "telco-assurance",
    "telco-cloud",
    "telco-container",
    "telco-lab",
    "telco-local",
}
_EXPECTED_NON_CLOSURE = {
    "gates": {"G2": "OPEN", "G4": "OPEN", "G5": "OPEN", "GATE_E": "OPEN"},
    "stages": {
        "P3e": "IN_PROGRESS",
        "P3e-5": "IN_PROGRESS",
        "P6": "NOT_STARTED",
        "P7": "IN_PROGRESS",
        "S2-04": "BLOCKED",
        "S4": "IN_PROGRESS",
        "S7": "IN_PROGRESS",
        "WORKFLOW_E": "IN_PROGRESS",
    },
}
_BIDI_CONTROLS = {
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
}
_MESSAGES = {
    "invalid_arguments": "command arguments are invalid",
    "offline_required": "explicit offline mode is required",
    "source_unavailable": "commit-bound source is unavailable",
    "source_not_clean": "tracked source is not clean",
    "source_mismatch": "source commit does not match the requested commit",
    "source_changed": "source changed during bundle construction",
    "ledger_read_failed": "submission evidence ledger could not be read safely",
    "ledger_contract_failed": "submission evidence ledger violated its fixed contract",
    "privacy_contract_failed": "submission bundle violated its privacy contract",
    "workspace_unsafe": "submission workspace is unsafe",
    "workspace_not_owned": "submission workspace is not marker-owned",
    "build_in_progress": "submission bundle construction is already in progress",
    "bundle_write_failed": "submission bundle could not be written safely",
    "bundle_contract_failed": "submission bundle violated its fixed contract",
    "cleanup_failed": "submission workspace cleanup failed safely",
    "command_failed": "submission bundle command failed safely",
}
# Private validation aliases collapse into the sixteen serialized command errors.
_INTERNAL_ERROR_CODE_ALIASES = {
    "source_invalid": "source_unavailable",
    "ledger_invalid": "ledger_contract_failed",
    "output_conflict": "workspace_not_owned",
    "unsafe_filesystem": "workspace_unsafe",
    "io_failure": "bundle_write_failed",
}


class SubmissionBundleError(Exception):
    """A fixed, non-disclosing command failure."""

    def __init__(self, code: str) -> None:
        mapped = _INTERNAL_ERROR_CODE_ALIASES.get(code, code)
        stable = mapped if mapped in _MESSAGES else "command_failed"
        self.code = stable
        super().__init__(_MESSAGES[stable])


class _FileIdentity(NamedTuple):
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class _DirectoryIdentity(NamedTuple):
    device: int
    inode: int
    mode: int


class _SourceSnapshot(NamedTuple):
    head_sha: str
    ledger_bytes: bytes
    ledger_sha256: str


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise SubmissionBundleError("invalid_arguments") from None


def _parse_arguments(arguments: Sequence[str]) -> None:
    if not arguments:
        raise SubmissionBundleError("offline_required") from None
    if tuple(arguments) != ("--offline",):
        raise SubmissionBundleError("invalid_arguments") from None
    parser = _SafeArgumentParser(
        prog="networkagent-local-submission-builder",
        add_help=False,
        allow_abbrev=False,
    )
    parser.add_argument("--offline", action="store_true")
    parsed = parser.parse_args(tuple(arguments))
    if parsed.offline is not True:
        raise SubmissionBundleError("invalid_arguments") from None


def _canonical_json_bytes(
    value: object, *, code: str = "ledger_contract_failed"
) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise SubmissionBundleError(code) from None
    return rendered.encode("utf-8") + b"\n"


def _write_json(stream: TextIO, value: object) -> None:
    stream.write(_canonical_json_bytes(value, code="command_failed").decode("utf-8"))


def _reject_number(_value: str) -> object:
    raise SubmissionBundleError("ledger_invalid") from None


def _parse_integer(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError:
        raise SubmissionBundleError("ledger_invalid") from None
    if abs(parsed) > _MAX_INTEGER:
        raise SubmissionBundleError("ledger_invalid") from None
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionBundleError("ledger_invalid") from None
        result[key] = value
    return result


def _validate_text(value: object, *, token: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise SubmissionBundleError("ledger_invalid") from None
    try:
        encoded = value.encode("utf-8", "strict")
        normalized = unicodedata.normalize("NFC", value)
    except (UnicodeError, ValueError):
        raise SubmissionBundleError("ledger_invalid") from None
    if len(encoded) > _MAX_STRING_BYTES or value != normalized:
        raise SubmissionBundleError("ledger_invalid") from None
    for character in value:
        ordinal = ord(character)
        if ordinal < 0x20 or 0x7F <= ordinal <= 0x9F:
            raise SubmissionBundleError("ledger_invalid") from None
        if ordinal in _BIDI_CONTROLS:
            raise SubmissionBundleError("ledger_invalid") from None
    if token and _TOKEN.fullmatch(value) is None:
        raise SubmissionBundleError("ledger_invalid") from None
    return value


def _validate_json_tree(value: object) -> None:
    remaining = _MAX_JSON_NODES
    stack = [value]
    while stack:
        remaining -= 1
        if remaining < 0:
            raise SubmissionBundleError("ledger_invalid") from None
        item = stack.pop()
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            if abs(item) > _MAX_INTEGER:
                raise SubmissionBundleError("ledger_invalid") from None
            continue
        if isinstance(item, float):
            raise SubmissionBundleError("ledger_invalid") from None
        if isinstance(item, str):
            _validate_text(item)
            continue
        if isinstance(item, list):
            stack.extend(item)
            continue
        if isinstance(item, dict):
            for key, nested in item.items():
                _validate_text(key)
                stack.append(nested)
            continue
        raise SubmissionBundleError("ledger_invalid") from None


def _require_keys(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SubmissionBundleError("ledger_invalid") from None
    return value


def _require_string_list(
    value: object,
    *,
    allow_empty: bool = False,
    tokens: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise SubmissionBundleError("ledger_invalid") from None
    result = [_validate_text(item, token=tokens) for item in value]
    if len(set(result)) != len(result):
        raise SubmissionBundleError("ledger_invalid") from None
    return result


def _validate_delivered(value: object) -> None:
    delivered = _require_keys(value, {"representation", "state", "value"})
    representation = delivered["representation"]
    state = delivered["state"]
    payload = delivered["value"]
    if representation not in {"TOKEN_LIST", "BOOLEAN_MAP", "ABSENT"}:
        raise SubmissionBundleError("ledger_invalid") from None
    if representation == "ABSENT":
        if state != "ABSENT_BY_SCHEMA" or payload is not None:
            raise SubmissionBundleError("ledger_invalid") from None
        return
    if state != "VERIFIED":
        raise SubmissionBundleError("ledger_invalid") from None
    if representation == "TOKEN_LIST":
        _require_string_list(payload, tokens=True)
        return
    if not isinstance(payload, dict) or not payload:
        raise SubmissionBundleError("ledger_invalid") from None
    for key, item in payload.items():
        _validate_text(key)
        if item is not True:
            raise SubmissionBundleError("ledger_invalid") from None


def _validate_evidence_classification(value: object, slice_id: str) -> None:
    classification = _require_keys(value, {"scope", "state", "value"})
    if classification != EXPECTED_EVIDENCE_CLASSIFICATION.get(slice_id):
        raise SubmissionBundleError("ledger_invalid") from None
    _validate_text(classification["scope"], token=True)
    _validate_text(classification["state"], token=True)
    if classification["value"] is not None:
        _validate_text(classification["value"], token=True)


def _validate_provenance(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise SubmissionBundleError("ledger_invalid") from None
    seen: set[str] = set()
    for item in value:
        record = _require_keys(item, {"commit", "role"})
        commit = record["commit"]
        if not isinstance(commit, str) or _COMMIT_SHA.fullmatch(commit) is None:
            raise SubmissionBundleError("ledger_invalid") from None
        _validate_text(record["role"], token=True)
        if commit in seen:
            raise SubmissionBundleError("ledger_invalid") from None
        seen.add(commit)


def _validate_evidence_docs(value: object) -> None:
    evidence = _require_keys(value, {"commit", "kind", "paths", "provenance"})
    kind = evidence["kind"]
    commit = evidence["commit"]
    if kind not in {"DEDICATED_POST_RC", "MIXED_CI_DOCS_RECORD"}:
        raise SubmissionBundleError("ledger_invalid") from None
    if not isinstance(commit, str) or _COMMIT_SHA.fullmatch(commit) is None:
        raise SubmissionBundleError("ledger_invalid") from None
    paths = _require_string_list(evidence["paths"])
    for item in paths:
        if (
            not item.startswith("docs/")
            or item.startswith("/")
            or "\\" in item
            or ".." in item.split("/")
            or item == "docs/课题组答辩项目报告.md"
        ):
            raise SubmissionBundleError("ledger_invalid") from None
    _validate_provenance(evidence["provenance"])
    provenance = evidence["provenance"]
    assert isinstance(provenance, list)
    if not any(
        isinstance(record, dict) and record.get("commit") == commit
        for record in provenance
    ):
        raise SubmissionBundleError("ledger_invalid") from None


def _validate_job(value: object, seen_job_ids: set[str]) -> None:
    job = _require_keys(value, {"conclusion", "id", "python"})
    if job["conclusion"] != "success":
        raise SubmissionBundleError("ledger_invalid") from None
    job_id = job["id"]
    if not isinstance(job_id, str) or _DECIMAL_ID.fullmatch(job_id) is None:
        raise SubmissionBundleError("ledger_invalid") from None
    if job_id in seen_job_ids:
        raise SubmissionBundleError("ledger_invalid") from None
    seen_job_ids.add(job_id)
    if job["python"] not in {"3.12", "3.13", None}:
        raise SubmissionBundleError("ledger_invalid") from None


def _validate_summary(value: object) -> None:
    summary = _require_keys(
        value,
        {"availability", "bytes", "name", "path", "schema", "sha256"},
    )
    name = _validate_text(summary["name"])
    path = _validate_text(summary["path"])
    schema_name = _validate_text(summary["schema"])
    if (
        re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.json", name) is None
        or path != f"release-evidence/{name}"
        or re.fullmatch(r"networkagent-[a-z0-9-]+/1\.0", schema_name) is None
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    availability = summary["availability"]
    if availability not in {"PRESENT", "NOT_EMITTED"}:
        raise SubmissionBundleError("ledger_invalid") from None
    byte_record = summary["bytes"]
    digest_record = summary["sha256"]
    if not isinstance(byte_record, dict) or not isinstance(digest_record, dict):
        raise SubmissionBundleError("ledger_invalid") from None
    byte_state = byte_record.get("state")
    digest_state = digest_record.get("state")
    if byte_state != digest_state:
        raise SubmissionBundleError("ledger_invalid") from None
    if byte_state == "VERIFIED":
        if set(byte_record) != {"state", "value"} or set(digest_record) != {
            "state",
            "value",
        }:
            raise SubmissionBundleError("ledger_invalid") from None
        byte_count = byte_record["value"]
        digest = digest_record["value"]
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count <= 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise SubmissionBundleError("ledger_invalid") from None
        if availability != "PRESENT":
            raise SubmissionBundleError("ledger_invalid") from None
        return
    if byte_state not in {"PRESENT_NOT_RECORDED", "NOT_EMITTED"}:
        raise SubmissionBundleError("ledger_invalid") from None
    if set(byte_record) != {"reason", "state", "value"} or set(digest_record) != {
        "reason",
        "state",
        "value",
    }:
        raise SubmissionBundleError("ledger_invalid") from None
    expected_reason = (
        "NOT_RECORDED" if byte_state == "PRESENT_NOT_RECORDED" else "NOT_EMITTED"
    )
    expected_availability = (
        "PRESENT" if byte_state == "PRESENT_NOT_RECORDED" else "NOT_EMITTED"
    )
    if (
        byte_record["reason"] != expected_reason
        or digest_record["reason"] != expected_reason
        or byte_record["value"] is not None
        or digest_record["value"] is not None
        or availability != expected_availability
    ):
        raise SubmissionBundleError("ledger_invalid") from None


def _validate_artifact(
    value: object,
    *,
    workflow: str,
    job_ids: set[str],
) -> None:
    artifact = _require_keys(
        value,
        {
            "archive_bytes",
            "archive_sha256",
            "exact_closure",
            "id",
            "name",
            "payload_content_embedded_in_ledger",
            "primary_summary",
            "publisher_job_id",
            "remote_availability_asserted",
            "retention_days",
            "verification_label",
        },
    )
    for key in ("id", "publisher_job_id"):
        item = artifact[key]
        if not isinstance(item, str) or _DECIMAL_ID.fullmatch(item) is None:
            raise SubmissionBundleError("ledger_invalid") from None
    if artifact["publisher_job_id"] not in job_ids:
        raise SubmissionBundleError("ledger_invalid") from None
    byte_count = artifact["archive_bytes"]
    if (
        isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    digest = artifact["archive_sha256"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise SubmissionBundleError("ledger_invalid") from None
    expected_name = f"{workflow}-release-py3.12-attempt-1"
    if artifact["name"] != expected_name:
        raise SubmissionBundleError("ledger_invalid") from None
    closure = _require_keys(
        artifact["exact_closure"],
        {"manifest_record_count", "member_count", "state"},
    )
    if closure["state"] == "VERIFIED":
        manifest_count = closure["manifest_record_count"]
        member_count = closure["member_count"]
        if (
            isinstance(manifest_count, bool)
            or not isinstance(manifest_count, int)
            or manifest_count <= 0
            or isinstance(member_count, bool)
            or not isinstance(member_count, int)
            or member_count != manifest_count + 1
        ):
            raise SubmissionBundleError("ledger_invalid") from None
    elif closure != {
        "manifest_record_count": None,
        "member_count": None,
        "state": "UNKNOWN",
    }:
        raise SubmissionBundleError("ledger_invalid") from None
    if (
        artifact["retention_days"] != 14
        or artifact["payload_content_embedded_in_ledger"] is not False
        or artifact["remote_availability_asserted"] is not False
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    label = _require_keys(
        artifact["verification_label"],
        {"github_api_native", "origin", "value"},
    )
    if label != {
        "github_api_native": False,
        "origin": "PROJECT_WORKFLOW_AND_INDEPENDENT_AUDIT",
        "value": "VERIFIED RC",
    }:
        raise SubmissionBundleError("ledger_invalid") from None
    _validate_summary(artifact["primary_summary"])


def _validate_runs(
    value: object,
    primary_run_id: str,
    rc_sha: str,
    slice_id: str,
    seen_run_ids: set[str],
    seen_job_ids: set[str],
    seen_artifact_ids: set[str],
) -> tuple[int, int, int]:
    if not isinstance(value, list) or not value:
        raise SubmissionBundleError("ledger_invalid") from None
    artifact_count = 0
    primary_seen = False
    job_count = 0
    for item in value:
        run = _require_keys(
            item,
            {
                "artifact",
                "head_sha",
                "jobs",
                "run_id",
                "trigger",
                "workflow",
            },
        )
        run_id = run["run_id"]
        if not isinstance(run_id, str) or _DECIMAL_ID.fullmatch(run_id) is None:
            raise SubmissionBundleError("ledger_invalid") from None
        if run_id in seen_run_ids:
            raise SubmissionBundleError("ledger_invalid") from None
        seen_run_ids.add(run_id)
        workflow = run["workflow"]
        if workflow not in _WORKFLOWS or run["head_sha"] != rc_sha:
            raise SubmissionBundleError("ledger_invalid") from None
        if run["trigger"] not in {"push", "workflow_dispatch"}:
            raise SubmissionBundleError("ledger_invalid") from None
        jobs = run["jobs"]
        if not isinstance(jobs, list) or not jobs:
            raise SubmissionBundleError("ledger_invalid") from None
        this_job_ids: set[str] = set()
        for job in jobs:
            before = set(seen_job_ids)
            _validate_job(job, seen_job_ids)
            this_job_ids.update(seen_job_ids - before)
            job_count += 1
        artifact = run["artifact"]
        if run_id == primary_run_id:
            primary_seen = True
            if artifact is None:
                raise SubmissionBundleError("ledger_invalid") from None
            _validate_artifact(artifact, workflow=workflow, job_ids=this_job_ids)
            assert isinstance(artifact, dict)
            closure = artifact["exact_closure"]
            summary = artifact["primary_summary"]
            assert isinstance(closure, dict) and isinstance(summary, dict)
            if (closure["state"] == "UNKNOWN") != (slice_id == "S7-01"):
                raise SubmissionBundleError("ledger_invalid") from None
            if (summary["availability"] == "NOT_EMITTED") != (slice_id == "S7-01"):
                raise SubmissionBundleError("ledger_invalid") from None
            summary_bytes = summary["bytes"]
            summary_sha256 = summary["sha256"]
            assert isinstance(summary_bytes, dict) and isinstance(summary_sha256, dict)
            expected_summary_state = (
                "NOT_EMITTED"
                if slice_id == "S7-01"
                else "PRESENT_NOT_RECORDED" if slice_id == "S4-05" else "VERIFIED"
            )
            if (
                summary_bytes["state"] != expected_summary_state
                or summary_sha256["state"] != expected_summary_state
            ):
                raise SubmissionBundleError("ledger_invalid") from None
            artifact_id = artifact["id"]
            assert isinstance(artifact_id, str)
            if artifact_id in seen_artifact_ids:
                raise SubmissionBundleError("ledger_invalid") from None
            seen_artifact_ids.add(artifact_id)
            publisher = artifact["publisher_job_id"]
            if not any(
                isinstance(job, dict)
                and job["id"] == publisher
                and job["python"] == "3.12"
                for job in jobs
            ) or not any(
                isinstance(job, dict) and job["python"] == "3.13" for job in jobs
            ):
                raise SubmissionBundleError("ledger_invalid") from None
            artifact_count += 1
        elif artifact is not None:
            raise SubmissionBundleError("ledger_invalid") from None
    if not primary_seen or artifact_count != 1:
        raise SubmissionBundleError("ledger_invalid") from None
    return len(value), job_count, artifact_count


def _validate_ledger(value: object) -> dict[str, object]:
    _validate_json_tree(value)
    ledger = _require_keys(
        value,
        {
            "evidence_as_of",
            "evidence_cutoff_commit",
            "non_closure",
            "repository",
            "schema_version",
            "slices",
        },
    )
    if (
        ledger["schema_version"] != LEDGER_SCHEMA
        or ledger["repository"] != REPOSITORY
        or ledger["evidence_as_of"] != EVIDENCE_AS_OF
        or ledger["evidence_cutoff_commit"] != EVIDENCE_CUTOFF_COMMIT
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    if ledger["non_closure"] != _EXPECTED_NON_CLOSURE:
        raise SubmissionBundleError("ledger_invalid") from None
    slices = ledger["slices"]
    if not isinstance(slices, list) or len(slices) != len(SLICE_IDS):
        raise SubmissionBundleError("ledger_invalid") from None
    ids: list[str] = []
    seen_run_ids: set[str] = set()
    seen_job_ids: set[str] = set()
    seen_artifact_ids: set[str] = set()
    run_count = 0
    job_count = 0
    artifact_count = 0
    for item in slices:
        entry = _require_keys(
            item,
            {
                "delivered",
                "evidence_classification",
                "evidence_docs",
                "gate_effect",
                "id",
                "not_claimed",
                "primary_run_id",
                "rc_sha",
                "runs",
                "title_zh",
            },
        )
        slice_id = _validate_text(entry["id"])
        ids.append(slice_id)
        if entry["rc_sha"] != EXPECTED_RC_SHA.get(slice_id):
            raise SubmissionBundleError("ledger_invalid") from None
        _validate_text(entry["title_zh"])
        gate_effect = _require_keys(
            entry["gate_effect"],
            {
                "overall_gates_closed",
                "overall_stages_closed",
                "scope",
                "slice_status",
                "stage_updates",
            },
        )
        if gate_effect != {
            "overall_gates_closed": [],
            "overall_stages_closed": [],
            "scope": "NARROW_SLICE_ONLY",
            "slice_status": "DONE",
            "stage_updates": EXPECTED_STAGE_UPDATES.get(slice_id, []),
        }:
            raise SubmissionBundleError("ledger_invalid") from None
        _validate_delivered(entry["delivered"])
        delivered = entry["delivered"]
        assert isinstance(delivered, dict)
        if (delivered["representation"] == "ABSENT") != (
            slice_id in {"S7-01", "S7-02", "S7-04"}
        ):
            raise SubmissionBundleError("ledger_invalid") from None
        _validate_evidence_classification(entry["evidence_classification"], slice_id)
        _validate_evidence_docs(entry["evidence_docs"])
        evidence_docs = entry["evidence_docs"]
        assert isinstance(evidence_docs, dict)
        if (evidence_docs["kind"] == "MIXED_CI_DOCS_RECORD") != (slice_id == "S7-01"):
            raise SubmissionBundleError("ledger_invalid") from None
        _require_string_list(entry["not_claimed"], tokens=True)
        primary_run_id = entry["primary_run_id"]
        if (
            not isinstance(primary_run_id, str)
            or _DECIMAL_ID.fullmatch(primary_run_id) is None
        ):
            raise SubmissionBundleError("ledger_invalid") from None
        counts = _validate_runs(
            entry["runs"],
            primary_run_id,
            entry["rc_sha"],
            slice_id,
            seen_run_ids,
            seen_job_ids,
            seen_artifact_ids,
        )
        run_count += counts[0]
        job_count += counts[1]
        artifact_count += counts[2]
    if (
        tuple(ids) != SLICE_IDS
        or run_count != 24
        or job_count != 66
        or artifact_count != 9
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    return ledger


_PRIVATE_KEY_NAMES = {
    "access_key",
    "access_token",
    "api_key",
    "authorization",
    "body",
    "checkpoint",
    "cookie",
    "credential",
    "database",
    "db_path",
    "event_id",
    "events",
    "ground_truth",
    "incident_id",
    "label",
    "labels",
    "local_path",
    "password",
    "private_id",
    "private_key",
    "ran_ue_id",
    "raw",
    "raw_data",
    "raw_payload",
    "refresh_token",
    "row",
    "rows",
    "secret",
    "seal",
    "source_event_id",
    "source_url",
    "token",
    "trace_id",
    "ue",
    "ue_id",
    "wheel",
}
_SUMMARY_PATH_CONTEXT = (
    "slices",
    "[]",
    "runs",
    "[]",
    "artifact",
    "primary_summary",
)
_SUMMARY_KEYS = {
    "availability",
    "bytes",
    "name",
    "path",
    "schema",
    "sha256",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:npm|hf)_[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{16,}"),
    re.compile(
        r"(?i)\b(?:access[_-]?token|api[_-]?key|password|passwd|"
        r"private[_-]?key|refresh[_-]?token|secret|token)\s*[:=]\s*"
        r"[^\s,;]{4,}"
    ),
)
_SCHEME_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{1,31}:" r"(?://|[\\/]|[A-Za-z0-9])"
)
_PROTOCOL_RELATIVE_PATTERN = re.compile(r"(?<![:\\])//[^\s<>\"']+")
_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"\\\\(?:[?.]|GLOBALROOT)[\\/]", re.IGNORECASE),
    re.compile(r"\\[?]{2}[\\/]"),
    re.compile(r"\\\\[^\s\\/]+[\\/]"),
    re.compile(r"(?<![<A-Za-z0-9._~-])~[\\/]"),
    re.compile(r"(?<![<\w.~-])/(?!/)[^\s<>\"'\\/:]+" r"(?:/[^\s<>\"'\\/:]+)*"),
)


def _text_violates_privacy_contract(value: str) -> bool:
    return (
        _SCHEME_PATTERN.search(value) is not None
        or _PROTOCOL_RELATIVE_PATTERN.search(value) is not None
        or any(pattern.search(value) is not None for pattern in _ABSOLUTE_PATH_PATTERNS)
        or any(pattern.search(value) is not None for pattern in _SECRET_VALUE_PATTERNS)
    )


def _validate_privacy_contract(value: object) -> None:
    stack: list[tuple[object, tuple[str, ...]]] = [(value, ())]
    while stack:
        item, context = stack.pop()
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
                if normalized == "path":
                    name = item.get("name")
                    if (
                        context != _SUMMARY_PATH_CONTEXT
                        or key != "path"
                        or set(item) != _SUMMARY_KEYS
                        or not isinstance(name, str)
                        or re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.json", name) is None
                        or nested != f"release-evidence/{name}"
                    ):
                        raise SubmissionBundleError("privacy_contract_failed") from None
                elif normalized in _PRIVATE_KEY_NAMES:
                    raise SubmissionBundleError("privacy_contract_failed") from None
                stack.append((nested, context + (normalized,)))
            continue
        if isinstance(item, list):
            stack.extend((nested, context + ("[]",)) for nested in item)
            continue
        if not isinstance(item, str):
            continue
        if _text_violates_privacy_contract(item):
            raise SubmissionBundleError("privacy_contract_failed") from None


def _parse_ledger_bytes(blob: bytes) -> dict[str, object]:
    if not blob or len(blob) > _LEDGER_MAX_BYTES or not blob.endswith(b"\n"):
        raise SubmissionBundleError("ledger_invalid") from None
    if blob.startswith(b"\xef\xbb\xbf") or b"\r" in blob or b"\x00" in blob:
        raise SubmissionBundleError("ledger_invalid") from None
    try:
        text = blob.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
            parse_int=_parse_integer,
        )
    except SubmissionBundleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise SubmissionBundleError("ledger_invalid") from None
    ledger = _validate_ledger(value)
    if _canonical_json_bytes(ledger) != blob:
        raise SubmissionBundleError("ledger_invalid") from None
    _validate_privacy_contract(ledger)
    return ledger


def _run_git(
    repository_root: Path,
    arguments: Sequence[str],
    *,
    code: str = "source_unavailable",
) -> bytes:
    try:
        root = Path(os.path.abspath(repository_root))
    except (OSError, TypeError, ValueError):
        raise SubmissionBundleError(code) from None
    if "*" in str(root):
        raise SubmissionBundleError(code) from None
    _validate_directory_ancestry(root, code)
    command = (
        "git",
        "--no-optional-locks",
        "--no-replace-objects",
        "-c",
        f"safe.directory={root}",
        "-c",
        "color.ui=false",
        "-c",
        "core.pager=cat",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.useReplaceRefs=false",
        *arguments,
    )
    git_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            env=git_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise SubmissionBundleError(code) from None
    if completed.returncode != 0:
        raise SubmissionBundleError(code) from None
    return completed.stdout


def _load_source_snapshot(
    repository_root: Path,
    environment: Mapping[str, str],
) -> tuple[_SourceSnapshot, dict[str, object]]:
    try:
        root = Path(os.path.abspath(repository_root))
    except (OSError, TypeError, ValueError):
        raise SubmissionBundleError("source_unavailable") from None
    _validate_directory_ancestry(root, "source_unavailable")
    try:
        top_level = (
            _run_git(root, ("rev-parse", "--show-toplevel"))
            .decode("utf-8", "strict")
            .strip()
        )
        head = (
            _run_git(root, ("rev-parse", "--verify", "HEAD^{commit}"))
            .decode("ascii", "strict")
            .strip()
        )
    except UnicodeDecodeError:
        raise SubmissionBundleError("source_unavailable") from None
    try:
        if not os.path.samefile(root, Path(top_level)):
            raise SubmissionBundleError("source_unavailable") from None
    except (OSError, ValueError):
        raise SubmissionBundleError("source_unavailable") from None
    if _COMMIT_SHA.fullmatch(head) is None:
        raise SubmissionBundleError("source_unavailable") from None
    github_sha = environment.get("GITHUB_SHA")
    if github_sha is not None and github_sha != head:
        raise SubmissionBundleError("source_mismatch") from None
    tracked = _run_git(root, ("status", "--porcelain=v1", "-z", "--untracked-files=no"))
    if tracked:
        raise SubmissionBundleError("source_not_clean") from None
    object_name = f"HEAD:{EVIDENCE_LEDGER.as_posix()}"
    size_bytes = _run_git(
        root, ("cat-file", "-s", object_name), code="ledger_read_failed"
    )
    try:
        size = int(size_bytes.decode("ascii", "strict").strip(), 10)
    except (UnicodeDecodeError, ValueError):
        raise SubmissionBundleError("ledger_read_failed") from None
    if size <= 0:
        raise SubmissionBundleError("ledger_read_failed") from None
    if size > _LEDGER_MAX_BYTES:
        raise SubmissionBundleError("ledger_contract_failed") from None
    blob = _run_git(root, ("show", object_name), code="ledger_read_failed")
    if len(blob) != size:
        raise SubmissionBundleError("ledger_read_failed") from None
    ledger = _parse_ledger_bytes(blob)
    ledger_sha256 = hashlib.sha256(blob).hexdigest()
    if ledger_sha256 != EXPECTED_LEDGER_SHA256:
        raise SubmissionBundleError("ledger_contract_failed") from None
    snapshot = _SourceSnapshot(
        head_sha=head,
        ledger_bytes=blob,
        ledger_sha256=ledger_sha256,
    )
    return snapshot, ledger


def _assert_source_unchanged(
    repository_root: Path,
    snapshot: _SourceSnapshot,
    environment: Mapping[str, str],
) -> None:
    _validate_directory_ancestry(repository_root, "source_changed")
    try:
        head = (
            _run_git(
                repository_root,
                ("rev-parse", "--verify", "HEAD^{commit}"),
                code="source_changed",
            )
            .decode("ascii", "strict")
            .strip()
        )
    except UnicodeDecodeError:
        raise SubmissionBundleError("source_changed") from None
    if head != snapshot.head_sha:
        raise SubmissionBundleError("source_changed") from None
    github_sha = environment.get("GITHUB_SHA")
    if github_sha is not None and github_sha != head:
        raise SubmissionBundleError("source_changed") from None
    tracked = _run_git(
        repository_root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=no"),
        code="source_changed",
    )
    if tracked:
        raise SubmissionBundleError("source_changed") from None
    object_name = f"HEAD:{EVIDENCE_LEDGER.as_posix()}"
    try:
        size = int(
            _run_git(
                repository_root,
                ("cat-file", "-s", object_name),
                code="source_changed",
            )
            .decode("ascii", "strict")
            .strip(),
            10,
        )
    except (UnicodeDecodeError, ValueError):
        raise SubmissionBundleError("source_changed") from None
    if size != len(snapshot.ledger_bytes) or size > _LEDGER_MAX_BYTES:
        raise SubmissionBundleError("source_changed") from None
    ledger_bytes = _run_git(
        repository_root, ("show", object_name), code="source_changed"
    )
    if ledger_bytes != snapshot.ledger_bytes:
        raise SubmissionBundleError("source_changed") from None


def _safe_runs_for_output(runs: object) -> list[dict[str, object]]:
    assert isinstance(runs, list)
    result: list[dict[str, object]] = []
    for item in runs:
        assert isinstance(item, dict)
        result.append(
            {
                "artifact": item["artifact"],
                "head_sha": item["head_sha"],
                "jobs": item["jobs"],
                "run_id": item["run_id"],
                "trigger": item["trigger"],
                "workflow": item["workflow"],
            }
        )
    return result


def _render_index(ledger: dict[str, object]) -> bytes:
    slices = []
    raw_slices = ledger["slices"]
    assert isinstance(raw_slices, list)
    for item in raw_slices:
        assert isinstance(item, dict)
        evidence_docs = item["evidence_docs"]
        assert isinstance(evidence_docs, dict)
        paths = evidence_docs["paths"]
        assert isinstance(paths, list)
        slices.append(
            {
                "delivered": item["delivered"],
                "evidence_classification": item["evidence_classification"],
                "evidence_docs": {
                    "commit": evidence_docs["commit"],
                    "document_count": len(paths),
                    "kind": evidence_docs["kind"],
                    "provenance": evidence_docs["provenance"],
                },
                "gate_effect": item["gate_effect"],
                "id": item["id"],
                "primary_run_id": item["primary_run_id"],
                "rc_sha": item["rc_sha"],
                "runs": _safe_runs_for_output(item["runs"]),
                "title_zh": item["title_zh"],
            }
        )
    return _canonical_json_bytes(
        {
            "artifact_citation_policy": {
                "payload_content_embedded_in_bundle": False,
                "reference_mode": "HISTORICAL_VERIFIED_REFERENCE",
                "remote_availability_asserted": False,
            },
            "evidence_as_of": ledger["evidence_as_of"],
            "evidence_cutoff_commit": ledger["evidence_cutoff_commit"],
            "non_closure": ledger["non_closure"],
            "repository": ledger["repository"],
            "schema": INDEX_SCHEMA,
            "slices": slices,
        },
        code="bundle_contract_failed",
    )


def _render_limitations(ledger: dict[str, object]) -> bytes:
    raw_slices = ledger["slices"]
    assert isinstance(raw_slices, list)
    return _canonical_json_bytes(
        {
            "evidence_as_of": ledger["evidence_as_of"],
            "evidence_cutoff_commit": ledger["evidence_cutoff_commit"],
            "non_closure": ledger["non_closure"],
            "schema": LIMITATIONS_SCHEMA,
            "slices": [
                {"id": item["id"], "not_claimed": item["not_claimed"]}
                for item in raw_slices
                if isinstance(item, dict)
            ],
        },
        code="bundle_contract_failed",
    )


def _render_reproduce() -> bytes:
    text = """# Reproduce the local submission bundle

Run this command from a clean, committed repository checkout:

```text
python tools/local-stack/build_submission_bundle.py --offline
```

The command performs no network access. It reads the canonical evidence ledger from
the current committed Git blob, requires a clean tracked tree, and binds the result
to the current commit. Untracked files do not affect the evidence source gate.

The bundle contains historical artifact citations only. Artifact payloads are not
embedded, and continuing remote availability is not asserted. The limitations file
is part of the submission evidence and must be reviewed with the index.

`manifest.json` is written last and is the acceptance and ownership marker. A repeat
against the same committed source is byte-identical and reports `changed=false`.
Existing links, unknown files, or different bytes are preserved and rejected.
"""
    return text.encode("utf-8")


def _render_html(ledger: dict[str, object]) -> bytes:
    raw_slices = ledger["slices"]
    assert isinstance(raw_slices, list)
    rows: list[str] = []
    for item in raw_slices:
        assert isinstance(item, dict)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['id']), quote=True)}</td>"
            f"<td>{html.escape(str(item['title_zh']), quote=True)}</td>"
            f"<td><code>{html.escape(str(item['rc_sha']), quote=True)}</code></td>"
            f"<td>{html.escape(str(item['primary_run_id']), quote=True)}</td>"
            "</tr>"
        )
    document = (
        """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NetworkAgent local submission evidence</title>
</head>
<body>
<h1>NetworkAgent local submission evidence</h1>
<p class="notice">Historical artifact citations only: payloads are not embedded and continuing remote availability is not asserted. Review all limitations before using this bundle.</p>
<nav><a href="submission-index.json">Submission index</a> · <a href="limitations.json">Limitations</a> · <a href="REPRODUCE.md">Reproduce</a> · <a href="manifest.json">Manifest</a></nav>
<table><thead><tr><th>Slice</th><th>Title</th><th>Tested RC</th><th>Primary run</th></tr></thead><tbody>
"""
        + "\n".join(rows)
        + """
</tbody></table>
</body>
</html>
"""
    )
    return document.encode("utf-8")


def _validate_public_evidence_docs(value: object, slice_id: str) -> None:
    evidence = _require_keys(
        value,
        {"commit", "document_count", "kind", "provenance"},
    )
    commit = evidence["commit"]
    if not isinstance(commit, str) or _COMMIT_SHA.fullmatch(commit) is None:
        raise SubmissionBundleError("ledger_invalid") from None
    expected_kind = (
        "MIXED_CI_DOCS_RECORD" if slice_id == "S7-01" else "DEDICATED_POST_RC"
    )
    expected_count = 2 if slice_id == "S7-01" else 1
    if (
        evidence["kind"] != expected_kind
        or isinstance(evidence["document_count"], bool)
        or evidence["document_count"] != expected_count
    ):
        raise SubmissionBundleError("ledger_invalid") from None
    _validate_provenance(evidence["provenance"])
    provenance = evidence["provenance"]
    assert isinstance(provenance, list)
    if not any(
        isinstance(record, dict) and record["commit"] == commit for record in provenance
    ):
        raise SubmissionBundleError("ledger_invalid") from None


def _validate_public_documents(
    index_value: object,
    limitations_value: object,
) -> None:
    try:
        _validate_json_tree(index_value)
        _validate_json_tree(limitations_value)
        index = _require_keys(
            index_value,
            {
                "artifact_citation_policy",
                "evidence_as_of",
                "evidence_cutoff_commit",
                "non_closure",
                "repository",
                "schema",
                "slices",
            },
        )
        limitations = _require_keys(
            limitations_value,
            {
                "evidence_as_of",
                "evidence_cutoff_commit",
                "non_closure",
                "schema",
                "slices",
            },
        )
        policy = _require_keys(
            index["artifact_citation_policy"],
            {
                "payload_content_embedded_in_bundle",
                "reference_mode",
                "remote_availability_asserted",
            },
        )
        if policy != {
            "payload_content_embedded_in_bundle": False,
            "reference_mode": "HISTORICAL_VERIFIED_REFERENCE",
            "remote_availability_asserted": False,
        }:
            raise SubmissionBundleError("ledger_invalid") from None
        if (
            index["schema"] != INDEX_SCHEMA
            or index["repository"] != REPOSITORY
            or index["evidence_as_of"] != EVIDENCE_AS_OF
            or index["evidence_cutoff_commit"] != EVIDENCE_CUTOFF_COMMIT
            or index["non_closure"] != _EXPECTED_NON_CLOSURE
            or limitations["schema"] != LIMITATIONS_SCHEMA
            or limitations["evidence_as_of"] != EVIDENCE_AS_OF
            or limitations["evidence_cutoff_commit"] != EVIDENCE_CUTOFF_COMMIT
            or limitations["non_closure"] != _EXPECTED_NON_CLOSURE
        ):
            raise SubmissionBundleError("ledger_invalid") from None

        index_slices = index["slices"]
        limitation_slices = limitations["slices"]
        if (
            not isinstance(index_slices, list)
            or len(index_slices) != len(SLICE_IDS)
            or not isinstance(limitation_slices, list)
            or len(limitation_slices) != len(SLICE_IDS)
        ):
            raise SubmissionBundleError("ledger_invalid") from None

        seen_run_ids: set[str] = set()
        seen_job_ids: set[str] = set()
        seen_artifact_ids: set[str] = set()
        run_count = 0
        job_count = 0
        artifact_count = 0
        ids: list[str] = []
        for index_item, limitation_item, expected_id in zip(
            index_slices,
            limitation_slices,
            SLICE_IDS,
        ):
            entry = _require_keys(
                index_item,
                {
                    "delivered",
                    "evidence_classification",
                    "evidence_docs",
                    "gate_effect",
                    "id",
                    "primary_run_id",
                    "rc_sha",
                    "runs",
                    "title_zh",
                },
            )
            limitation = _require_keys(
                limitation_item,
                {"id", "not_claimed"},
            )
            slice_id = _validate_text(entry["id"])
            ids.append(slice_id)
            if (
                slice_id != expected_id
                or limitation["id"] != slice_id
                or entry["rc_sha"] != EXPECTED_RC_SHA[slice_id]
            ):
                raise SubmissionBundleError("ledger_invalid") from None
            _validate_text(entry["title_zh"])
            _require_string_list(limitation["not_claimed"], tokens=True)
            _validate_delivered(entry["delivered"])
            delivered = entry["delivered"]
            assert isinstance(delivered, dict)
            if (delivered["representation"] == "ABSENT") != (
                slice_id in {"S7-01", "S7-02", "S7-04"}
            ):
                raise SubmissionBundleError("ledger_invalid") from None
            _validate_evidence_classification(
                entry["evidence_classification"],
                slice_id,
            )
            _validate_public_evidence_docs(entry["evidence_docs"], slice_id)
            gate_effect = _require_keys(
                entry["gate_effect"],
                {
                    "overall_gates_closed",
                    "overall_stages_closed",
                    "scope",
                    "slice_status",
                    "stage_updates",
                },
            )
            if gate_effect != {
                "overall_gates_closed": [],
                "overall_stages_closed": [],
                "scope": "NARROW_SLICE_ONLY",
                "slice_status": "DONE",
                "stage_updates": EXPECTED_STAGE_UPDATES.get(slice_id, []),
            }:
                raise SubmissionBundleError("ledger_invalid") from None
            primary_run_id = entry["primary_run_id"]
            if (
                not isinstance(primary_run_id, str)
                or _DECIMAL_ID.fullmatch(primary_run_id) is None
            ):
                raise SubmissionBundleError("ledger_invalid") from None
            counts = _validate_runs(
                entry["runs"],
                primary_run_id,
                entry["rc_sha"],
                slice_id,
                seen_run_ids,
                seen_job_ids,
                seen_artifact_ids,
            )
            run_count += counts[0]
            job_count += counts[1]
            artifact_count += counts[2]
        if (
            tuple(ids) != SLICE_IDS
            or run_count != 24
            or job_count != 66
            or artifact_count != 9
        ):
            raise SubmissionBundleError("ledger_invalid") from None
        if (
            hashlib.sha256(
                _canonical_json_bytes(index, code="bundle_contract_failed")
            ).hexdigest()
            != EXPECTED_INDEX_SHA256
            or hashlib.sha256(
                _canonical_json_bytes(limitations, code="bundle_contract_failed")
            ).hexdigest()
            != EXPECTED_LIMITATIONS_SHA256
        ):
            raise SubmissionBundleError("ledger_invalid") from None
    except SubmissionBundleError:
        raise SubmissionBundleError("bundle_contract_failed") from None


def _validate_rendered_outputs(files: Mapping[str, bytes]) -> None:
    if tuple(files) != OUTPUT_FILENAMES:
        raise SubmissionBundleError("bundle_contract_failed") from None
    total = 0
    texts: dict[str, str] = {}
    parsed_json: dict[str, object] = {}
    for name, payload in files.items():
        if not payload or len(payload) > _OUTPUT_FILE_MAX_BYTES:
            raise SubmissionBundleError("bundle_contract_failed") from None
        total += len(payload)
        if (
            payload.startswith(b"\xef\xbb\xbf")
            or b"\r" in payload
            or b"\x00" in payload
        ):
            raise SubmissionBundleError("bundle_contract_failed") from None
        try:
            text = payload.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise SubmissionBundleError("bundle_contract_failed") from None
        texts[name] = text
        if _text_violates_privacy_contract(text):
            raise SubmissionBundleError("privacy_contract_failed") from None
        if name.endswith(".json"):
            try:
                parsed = json.loads(
                    text,
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_number,
                    parse_float=_reject_number,
                    parse_int=_parse_integer,
                )
            except SubmissionBundleError:
                raise SubmissionBundleError("bundle_contract_failed") from None
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise SubmissionBundleError("bundle_contract_failed") from None
            if _canonical_json_bytes(parsed, code="bundle_contract_failed") != payload:
                raise SubmissionBundleError("bundle_contract_failed") from None
            _validate_privacy_contract(parsed)
            parsed_json[name] = parsed
    if total > _OUTPUT_TOTAL_MAX_BYTES:
        raise SubmissionBundleError("bundle_contract_failed") from None

    index = parsed_json.get("submission-index.json")
    limitations = parsed_json.get("limitations.json")
    if not isinstance(index, dict) or set(index) != {
        "artifact_citation_policy",
        "evidence_as_of",
        "evidence_cutoff_commit",
        "non_closure",
        "repository",
        "schema",
        "slices",
    }:
        raise SubmissionBundleError("bundle_contract_failed") from None
    if not isinstance(limitations, dict) or set(limitations) != {
        "evidence_as_of",
        "evidence_cutoff_commit",
        "non_closure",
        "schema",
        "slices",
    }:
        raise SubmissionBundleError("bundle_contract_failed") from None
    if (
        index["schema"] != INDEX_SCHEMA
        or index["repository"] != REPOSITORY
        or index["evidence_as_of"] != EVIDENCE_AS_OF
        or index["evidence_cutoff_commit"] != EVIDENCE_CUTOFF_COMMIT
        or index["non_closure"] != _EXPECTED_NON_CLOSURE
        or limitations["schema"] != LIMITATIONS_SCHEMA
        or limitations["evidence_as_of"] != EVIDENCE_AS_OF
        or limitations["evidence_cutoff_commit"] != EVIDENCE_CUTOFF_COMMIT
        or limitations["non_closure"] != _EXPECTED_NON_CLOSURE
        or index["artifact_citation_policy"]
        != {
            "payload_content_embedded_in_bundle": False,
            "reference_mode": "HISTORICAL_VERIFIED_REFERENCE",
            "remote_availability_asserted": False,
        }
    ):
        raise SubmissionBundleError("bundle_contract_failed") from None
    index_slices = index["slices"]
    limitation_slices = limitations["slices"]
    index_slice_keys = {
        "delivered",
        "evidence_classification",
        "evidence_docs",
        "gate_effect",
        "id",
        "primary_run_id",
        "rc_sha",
        "runs",
        "title_zh",
    }
    if (
        not isinstance(index_slices, list)
        or len(index_slices) != len(SLICE_IDS)
        or any(
            not isinstance(item, dict) or set(item) != index_slice_keys
            for item in index_slices
        )
        or tuple(item["id"] for item in index_slices) != SLICE_IDS
        or any(
            item["evidence_classification"]
            != EXPECTED_EVIDENCE_CLASSIFICATION[item["id"]]
            for item in index_slices
        )
        or any(
            not isinstance(item["evidence_docs"], dict)
            or set(item["evidence_docs"])
            != {"commit", "document_count", "kind", "provenance"}
            for item in index_slices
        )
        or not isinstance(limitation_slices, list)
        or len(limitation_slices) != len(SLICE_IDS)
        or any(
            not isinstance(item, dict) or set(item) != {"id", "not_claimed"}
            for item in limitation_slices
        )
        or tuple(item["id"] for item in limitation_slices) != SLICE_IDS
    ):
        raise SubmissionBundleError("bundle_contract_failed") from None
    _validate_public_documents(index, limitations)

    manifest = parsed_json.get("manifest.json")
    if not isinstance(manifest, dict) or set(manifest) != {
        "files",
        "ownership",
        "schema",
    }:
        raise SubmissionBundleError("bundle_contract_failed") from None
    ownership = manifest["ownership"]
    records = manifest["files"]
    if (
        manifest["schema"] != MANIFEST_SCHEMA
        or not isinstance(ownership, dict)
        or set(ownership) != {"ledger_sha256", "marker", "repository", "source_commit"}
        or ownership["ledger_sha256"] != EXPECTED_LEDGER_SHA256
        or ownership["marker"] != "NETWORKAGENT_LOCAL_SUBMISSION_BUNDLE"
        or ownership["repository"] != REPOSITORY
        or not isinstance(ownership["source_commit"], str)
        or _COMMIT_SHA.fullmatch(ownership["source_commit"]) is None
        or not isinstance(records, list)
        or len(records) != len(OUTPUT_FILENAMES) - 1
    ):
        raise SubmissionBundleError("bundle_contract_failed") from None
    for record, name in zip(records, OUTPUT_FILENAMES[:-1]):
        payload = files[name]
        if record != {
            "bytes": len(payload),
            "name": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }:
            raise SubmissionBundleError("bundle_contract_failed") from None

    page = texts["index.html"]
    expected_csp = (
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'\">"
    )
    csp_tags = re.findall(
        r"<meta\b[^>]*\bhttp-equiv\s*=\s*"
        r"(?:\"Content-Security-Policy\"|'Content-Security-Policy')"
        r"[^>]*>",
        page,
        flags=re.IGNORECASE,
    )
    if csp_tags != [expected_csp]:
        raise SubmissionBundleError("bundle_contract_failed") from None
    if (
        re.search(
            r"<\s*/?\s*(?:base|iframe|object|embed|script|img|form|style|"
            r"link|video|audio|svg)\b",
            page,
            flags=re.IGNORECASE,
        )
        is not None
        or re.search(
            r"<meta\b[^>]*\bhttp-equiv\s*=\s*(?:\"refresh\"|'refresh'|refresh)",
            page,
            flags=re.IGNORECASE,
        )
        is not None
        or re.search(
            r"\b(?:on[a-z0-9_-]+|src|srcset|style)\s*=",
            page,
            flags=re.IGNORECASE,
        )
        is not None
    ):
        raise SubmissionBundleError("privacy_contract_failed") from None
    expected_links = (
        "submission-index.json",
        "limitations.json",
        "REPRODUCE.md",
        "manifest.json",
    )
    anchor_tags = re.findall(r"<a\b[^>]*>", page, flags=re.IGNORECASE)
    href_matches = re.findall(r"\bhref\s*=\s*([\"'])(.*?)\1", page, flags=re.IGNORECASE)
    links = tuple(value for _quote, value in href_matches)
    if (
        len(anchor_tags) != 4
        or len(href_matches) != 4
        or links != expected_links
        or any(
            re.fullmatch(r'<a href="[^"/\\:#]+">', tag) is None for tag in anchor_tags
        )
    ):
        raise SubmissionBundleError("bundle_contract_failed") from None

    reproduce = texts["REPRODUCE.md"]
    if (
        re.search(
            r"<\s*/?\s*(?:base|iframe|object|embed|script|img|form|style|"
            r"link|video|audio|svg|meta)\b",
            reproduce,
            flags=re.IGNORECASE,
        )
        is not None
        or re.search(
            r"\b(?:on[a-z0-9_-]+|href|src|srcset|style)\s*=",
            reproduce,
            flags=re.IGNORECASE,
        )
        is not None
        or re.search(r"!?\[[^\]\n]*\]\([^\)\n]+\)", reproduce) is not None
    ):
        raise SubmissionBundleError("privacy_contract_failed") from None


def _render_bundle(
    ledger: dict[str, object], snapshot: _SourceSnapshot
) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "submission-index.json": _render_index(ledger),
        "limitations.json": _render_limitations(ledger),
        "REPRODUCE.md": _render_reproduce(),
        "index.html": _render_html(ledger),
    }
    manifest = {
        "files": [
            {
                "bytes": len(files[name]),
                "name": name,
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in OUTPUT_FILENAMES[:-1]
        ],
        "ownership": {
            "ledger_sha256": snapshot.ledger_sha256,
            "marker": "NETWORKAGENT_LOCAL_SUBMISSION_BUNDLE",
            "repository": REPOSITORY,
            "source_commit": snapshot.head_sha,
        },
        "schema": MANIFEST_SCHEMA,
    }
    files["manifest.json"] = _canonical_json_bytes(
        manifest, code="bundle_contract_failed"
    )
    _validate_rendered_outputs(files)
    return files


def _is_reparse(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & flag)


def _directory_identity(
    path: Path, code: str = "unsafe_filesystem"
) -> _DirectoryIdentity:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise SubmissionBundleError(code) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
    ):
        raise SubmissionBundleError(code) from None
    return _DirectoryIdentity(metadata.st_dev, metadata.st_ino, metadata.st_mode)


def _validate_directory_ancestry(path: Path, code: str) -> None:
    current = path
    while True:
        _directory_identity(current, code)
        parent = current.parent
        if parent == current:
            return
        current = parent


def _require_directory_identity(path: Path, expected: _DirectoryIdentity) -> None:
    if _directory_identity(path) != expected:
        raise SubmissionBundleError("unsafe_filesystem") from None


def _file_identity(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _same_file_object(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    """Compare handle/path metadata while retaining path ctime as the owner token."""

    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
        left.st_nlink,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
        right.st_nlink,
    )


def _same_identity_object(left: _FileIdentity, right: _FileIdentity) -> bool:
    return left[:-1] == right[:-1]


def _validate_regular_metadata(metadata: os.stat_result, *, links: int = 1) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != links
    ):
        raise SubmissionBundleError("unsafe_filesystem") from None


def _lstat_owned_file(path: Path, expected: _FileIdentity, *, links: int = 1) -> None:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None
    _validate_regular_metadata(metadata, links=links)
    if _file_identity(metadata) != expected:
        raise SubmissionBundleError("unsafe_filesystem") from None


def _read_bound_file(path: Path, maximum: int) -> tuple[bytes, _FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None
    try:
        before = os.fstat(descriptor)
        _validate_regular_metadata(before)
        path_before = os.lstat(path)
        _validate_regular_metadata(path_before)
        identity = _file_identity(path_before)
        if not _same_file_object(before, path_before) or before.st_size > maximum:
            raise SubmissionBundleError("unsafe_filesystem") from None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise SubmissionBundleError("unsafe_filesystem") from None
        after = os.fstat(descriptor)
        path_after = os.lstat(path)
        _validate_regular_metadata(after)
        _validate_regular_metadata(path_after)
        if (
            not _same_file_object(before, after)
            or not _same_file_object(after, path_after)
            or _file_identity(path_after) != identity
        ):
            raise SubmissionBundleError("unsafe_filesystem") from None
        return b"".join(chunks), identity
    except SubmissionBundleError:
        raise
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise SubmissionBundleError("unsafe_filesystem") from None


def _exclusive_write(
    path: Path,
    payload: bytes,
    *,
    exists_code: str = "workspace_unsafe",
) -> _FileIdentity:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise SubmissionBundleError(exists_code) from None
    except OSError:
        raise SubmissionBundleError("bundle_write_failed") from None
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise SubmissionBundleError("io_failure") from None
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _validate_regular_metadata(metadata)
        path_metadata = os.lstat(path)
        _validate_regular_metadata(path_metadata)
        if not _same_file_object(metadata, path_metadata):
            raise SubmissionBundleError("unsafe_filesystem") from None
        identity = _file_identity(path_metadata)
    except SubmissionBundleError:
        raise
    except OSError:
        raise SubmissionBundleError("io_failure") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise SubmissionBundleError("io_failure") from None
    readback, read_identity = _read_bound_file(path, len(payload))
    if readback != payload or read_identity != identity:
        raise SubmissionBundleError("unsafe_filesystem") from None
    return identity


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if os.name == "nt":
            return
        raise SubmissionBundleError("io_failure") from None
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise SubmissionBundleError("io_failure") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise SubmissionBundleError("io_failure") from None


def _list_directory(path: Path, expected: _DirectoryIdentity) -> tuple[str, ...]:
    _require_directory_identity(path, expected)
    try:
        names = tuple(sorted(os.listdir(path)))
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None
    _require_directory_identity(path, expected)
    return names


def _parse_owned_manifest(blob: bytes) -> dict[str, object]:
    if (
        not blob
        or len(blob) > _OUTPUT_FILE_MAX_BYTES
        or not blob.endswith(b"\n")
        or blob.startswith(b"\xef\xbb\xbf")
        or b"\r" in blob
        or b"\x00" in blob
    ):
        raise SubmissionBundleError("workspace_not_owned") from None
    try:
        value = json.loads(
            blob.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
            parse_int=_parse_integer,
        )
        if _canonical_json_bytes(value, code="workspace_not_owned") != blob:
            raise SubmissionBundleError("workspace_not_owned") from None
        manifest = _require_keys(value, {"files", "ownership", "schema"})
    except SubmissionBundleError:
        raise SubmissionBundleError("workspace_not_owned") from None
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise SubmissionBundleError("workspace_not_owned") from None
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise SubmissionBundleError("workspace_not_owned") from None
    ownership = manifest["ownership"]
    if not isinstance(ownership, dict) or set(ownership) != {
        "ledger_sha256",
        "marker",
        "repository",
        "source_commit",
    }:
        raise SubmissionBundleError("workspace_not_owned") from None
    if (
        ownership["marker"] != "NETWORKAGENT_LOCAL_SUBMISSION_BUNDLE"
        or ownership["repository"] != REPOSITORY
        or not isinstance(ownership["source_commit"], str)
        or _COMMIT_SHA.fullmatch(ownership["source_commit"]) is None
        or not isinstance(ownership["ledger_sha256"], str)
        or _SHA256.fullmatch(ownership["ledger_sha256"]) is None
    ):
        raise SubmissionBundleError("workspace_not_owned") from None
    records = manifest["files"]
    if not isinstance(records, list) or len(records) != len(OUTPUT_FILENAMES) - 1:
        raise SubmissionBundleError("workspace_not_owned") from None
    names: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"bytes", "name", "sha256"}:
            raise SubmissionBundleError("workspace_not_owned") from None
        name = record["name"]
        byte_count = record["bytes"]
        digest = record["sha256"]
        if (
            not isinstance(name, str)
            or isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count <= 0
            or byte_count > _OUTPUT_FILE_MAX_BYTES
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise SubmissionBundleError("workspace_not_owned") from None
        names.append(name)
    if tuple(names) != OUTPUT_FILENAMES[:-1]:
        raise SubmissionBundleError("workspace_not_owned") from None
    return manifest


def _verify_existing_output(path: Path, expected: Mapping[str, bytes]) -> bool:
    try:
        directory = _directory_identity(path)
    except SubmissionBundleError:
        raise SubmissionBundleError("workspace_not_owned") from None
    if _list_directory(path, directory) != tuple(sorted(OUTPUT_FILENAMES)):
        raise SubmissionBundleError("workspace_not_owned") from None
    identities: dict[str, _FileIdentity] = {}
    payloads: dict[str, bytes] = {}
    try:
        manifest_bytes, manifest_identity = _read_bound_file(
            path / "manifest.json", _OUTPUT_FILE_MAX_BYTES
        )
        manifest = _parse_owned_manifest(manifest_bytes)
        identities["manifest.json"] = manifest_identity
        payloads["manifest.json"] = manifest_bytes
        records = manifest["files"]
        assert isinstance(records, list)
        for record in records:
            assert isinstance(record, dict)
            name = str(record["name"])
            payload, identity = _read_bound_file(path / name, _OUTPUT_FILE_MAX_BYTES)
            if (
                len(payload) != record["bytes"]
                or hashlib.sha256(payload).hexdigest() != record["sha256"]
            ):
                raise SubmissionBundleError("workspace_not_owned") from None
            identities[name] = identity
            payloads[name] = payload
    except SubmissionBundleError:
        raise SubmissionBundleError("workspace_not_owned") from None
    if _list_directory(path, directory) != tuple(sorted(OUTPUT_FILENAMES)):
        raise SubmissionBundleError("workspace_not_owned") from None
    for name, identity in identities.items():
        try:
            _lstat_owned_file(path / name, identity)
        except SubmissionBundleError:
            raise SubmissionBundleError("workspace_not_owned") from None
    if any(payloads[name] != expected[name] for name in OUTPUT_FILENAMES):
        raise SubmissionBundleError("bundle_contract_failed") from None
    return True


def _path_exists_without_follow(path: Path) -> bool:
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None


def _ensure_local_directory(
    repository_root: Path,
) -> tuple[Path, _DirectoryIdentity]:
    local = repository_root / OUTPUT_DIRECTORY.parent
    if not _path_exists_without_follow(local):
        try:
            os.mkdir(local, 0o700)
        except FileExistsError:
            pass
        except OSError:
            raise SubmissionBundleError("io_failure") from None
    return local, _directory_identity(local)


def _acquire_lock(path: Path) -> _FileIdentity:
    if _path_exists_without_follow(path):
        try:
            payload, _ = _read_bound_file(path, len(_LOCK_BYTES))
        except SubmissionBundleError:
            raise SubmissionBundleError("workspace_unsafe") from None
        if payload != _LOCK_BYTES:
            raise SubmissionBundleError("workspace_unsafe") from None
        raise SubmissionBundleError("build_in_progress") from None
    try:
        return _exclusive_write(
            path,
            _LOCK_BYTES,
            exists_code="build_in_progress",
        )
    except SubmissionBundleError as error:
        if error.code != "build_in_progress" and _path_exists_without_follow(path):
            raise SubmissionBundleError("cleanup_failed") from None
        raise


def _safe_unlink(path: Path, expected: _FileIdentity, *, links: int = 1) -> None:
    _lstat_owned_file(path, expected, links=links)
    try:
        os.unlink(path)
    except OSError:
        raise SubmissionBundleError("unsafe_filesystem") from None


def _cleanup_owned_file(path: Path, expected: _FileIdentity) -> bool:
    try:
        _safe_unlink(path, expected)
        return True
    except SubmissionBundleError:
        return False


def _cleanup_owned_directory(
    path: Path,
    expected_directory: _DirectoryIdentity | None,
    expected_files: Mapping[str, _FileIdentity],
) -> bool:
    if expected_directory is None:
        return True
    try:
        names = _list_directory(path, expected_directory)
        if not set(names).issubset(expected_files):
            return False
        for name in names:
            _safe_unlink(path / name, expected_files[name])
        if _list_directory(path, expected_directory):
            return False
        os.rmdir(path)
        return True
    except (OSError, SubmissionBundleError):
        return False


def _publish_new_output(
    local: Path,
    local_identity: _DirectoryIdentity,
    output: Path,
    files: Mapping[str, bytes],
    source_check: Callable[[], None],
) -> bool:
    lock = local / ".networkagent-submission.lock"
    stage = local / ".networkagent-submission.staging"
    lock_identity: _FileIdentity | None = None
    stage_identity: _DirectoryIdentity | None = None
    output_identity: _DirectoryIdentity | None = None
    stage_files: dict[str, _FileIdentity] = {}
    output_files: dict[str, _FileIdentity] = {}
    failure: BaseException | None = None
    try:
        lock_identity = _acquire_lock(lock)
        _require_directory_identity(local, local_identity)
        if _path_exists_without_follow(stage):
            raise SubmissionBundleError("workspace_unsafe") from None
        if _path_exists_without_follow(output):
            try:
                _verify_existing_output(output, files)
                source_check()
            except BaseException as error:
                failure = error
            lock_clean = _cleanup_owned_file(lock, lock_identity)
            lock_identity = None
            try:
                _fsync_directory(local)
            except SubmissionBundleError:
                lock_clean = False
            if not lock_clean:
                raise SubmissionBundleError("cleanup_failed") from None
            if failure is not None:
                raise failure
            return False
        try:
            os.mkdir(stage, 0o700)
        except FileExistsError:
            raise SubmissionBundleError("workspace_unsafe") from None
        except OSError:
            raise SubmissionBundleError("bundle_write_failed") from None
        stage_identity = _directory_identity(stage)
        for name in OUTPUT_FILENAMES:
            stage_files[name] = _exclusive_write(stage / name, files[name])
        if _list_directory(stage, stage_identity) != tuple(sorted(OUTPUT_FILENAMES)):
            raise SubmissionBundleError("unsafe_filesystem") from None
        _fsync_directory(stage)
        _require_directory_identity(local, local_identity)
        try:
            os.mkdir(output, 0o700)
        except FileExistsError:
            raise SubmissionBundleError("workspace_not_owned") from None
        except OSError:
            raise SubmissionBundleError("bundle_write_failed") from None
        output_identity = _directory_identity(output)
        for name in OUTPUT_FILENAMES:
            if name == "manifest.json":
                source_check()
            source = stage / name
            target = output / name
            _lstat_owned_file(source, stage_files[name])
            try:
                os.link(source, target, follow_symlinks=False)
            except FileExistsError:
                raise SubmissionBundleError("workspace_unsafe") from None
            except OSError:
                raise SubmissionBundleError("bundle_write_failed") from None
            try:
                linked = os.lstat(source)
                target_linked = os.lstat(target)
            except OSError:
                raise SubmissionBundleError("workspace_unsafe") from None
            _validate_regular_metadata(linked, links=2)
            _validate_regular_metadata(target_linked, links=2)
            linked_identity = _file_identity(linked)
            if _file_identity(target_linked) != linked_identity:
                raise SubmissionBundleError("unsafe_filesystem") from None
            _safe_unlink(source, linked_identity, links=2)
            payload, published_identity = _read_bound_file(
                target, _OUTPUT_FILE_MAX_BYTES
            )
            if payload != files[name] or not _same_identity_object(
                linked_identity, published_identity
            ):
                raise SubmissionBundleError("unsafe_filesystem") from None
            output_files[name] = published_identity
            stage_files.pop(name)
        if _list_directory(stage, stage_identity):
            raise SubmissionBundleError("unsafe_filesystem") from None
        try:
            os.rmdir(stage)
        except OSError:
            raise SubmissionBundleError("unsafe_filesystem") from None
        stage_identity = None
        _fsync_directory(output)
        _fsync_directory(local)
        try:
            _verify_existing_output(output, files)
        except SubmissionBundleError as error:
            if error.code == "workspace_not_owned":
                raise SubmissionBundleError("bundle_contract_failed") from None
            raise
        source_check()
    except BaseException as error:
        failure = error

    cleanup_ok = True
    if failure is not None:
        output_clean = _cleanup_owned_directory(output, output_identity, output_files)
        stage_clean = _cleanup_owned_directory(stage, stage_identity, stage_files)
        cleanup_ok = output_clean and stage_clean
    if lock_identity is not None:
        cleanup_ok = _cleanup_owned_file(lock, lock_identity) and cleanup_ok
        try:
            _fsync_directory(local)
        except SubmissionBundleError:
            cleanup_ok = False
    if not cleanup_ok:
        raise SubmissionBundleError("cleanup_failed") from None
    if failure is not None:
        raise failure
    return True


def _build(repository_root: Path, environment: Mapping[str, str]) -> dict[str, object]:
    snapshot, ledger = _load_source_snapshot(repository_root, environment)
    files = _render_bundle(ledger, snapshot)
    root = Path(os.path.abspath(repository_root))
    _assert_source_unchanged(root, snapshot, environment)
    local, local_identity = _ensure_local_directory(root)
    output = root / OUTPUT_DIRECTORY
    changed = _publish_new_output(
        local,
        local_identity,
        output,
        files,
        lambda: _assert_source_unchanged(root, snapshot, environment),
    )
    _assert_source_unchanged(root, snapshot, environment)
    manifest = files["manifest.json"]
    return {
        "bundle": {
            "files": len(OUTPUT_FILENAMES),
            "manifest_bytes": len(manifest),
            "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "relative_directory": OUTPUT_DIRECTORY.as_posix(),
        },
        "changed": changed,
        "classification": "COMMIT_BOUND_LOCAL_SUBMISSION_BUNDLE",
        "ok": True,
        "schema": RESULT_SCHEMA,
        "source": {
            "commit_bound": True,
            "commit_sha": snapshot.head_sha,
            "tracked_clean": True,
        },
    }


def main(
    arguments: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    repository_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Build once, or verify an existing byte-identical committed-source bundle."""

    try:
        _parse_arguments(sys.argv[1:] if arguments is None else arguments)
        summary = _build(
            Path.cwd() if repository_root is None else repository_root,
            os.environ if environment is None else environment,
        )
        _write_json(stdout, summary)
        return 0
    except SubmissionBundleError as error:
        _write_json(
            stderr,
            {
                "error": {"code": error.code, "message": str(error)},
                "ok": False,
                "schema": ERROR_SCHEMA,
            },
        )
        return 2
    except BaseException:
        _write_json(
            stderr,
            {
                "error": {
                    "code": "command_failed",
                    "message": _MESSAGES["command_failed"],
                },
                "ok": False,
                "schema": ERROR_SCHEMA,
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
