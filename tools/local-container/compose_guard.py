#!/usr/bin/env python3
"""Fail-closed policy validator for the resolved Local Compose model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from typing import Mapping, Sequence


EXPECTED_SERVICES = {"assurance", "init", "reset", "probe", "smoke"}
EXPECTED_IMAGE = "networkagent-local:dev"
EXPECTED_PLATFORM = "linux/amd64"
EXPECTED_DOCKERFILE_SHA256 = (
    "8502e3ddbeba450a26a4cae3d7bd33d806ce422a5f78558e64d855bebeee73a2"
)
EXPECTED_DOCKERIGNORE_SHA256 = (
    "ed0b8ba014857ffcc2449c379fdb0ae2b030728ac345faa5674bf72910c41b20"
)
EXPECTED_NETWORK_MODES = {
    "assurance": "none",
    "init": "none",
    "reset": "none",
    "probe": "service:assurance",
    "smoke": "service:assurance",
}
EXPECTED_COMMANDS = {
    "assurance": ["serve"],
    "init": ["init"],
    "reset": ["reset"],
    "probe": ["probe"],
    "smoke": ["smoke"],
}
EXPECTED_PROFILES = {
    "init": ["ops"],
    "reset": ["ops"],
    "probe": ["diagnostics"],
    "smoke": ["test"],
}
WORKSPACE_TARGET = "/var/lib/networkagent"
INPUT_TARGETS = {
    "/opt/networkagent/data/samples/lte-demo/performance.csv": (
        "data/samples/lte-demo/performance.csv"
    ),
    "/opt/networkagent/data/samples/lte-demo/safe-cell-traces.csv": (
        "data/samples/lte-demo/safe-cell-traces.csv"
    ),
    "/opt/networkagent/data/rca-rules/lte": "data/rca-rules/lte",
    "/opt/networkagent/data/docs/lte": "data/docs/lte",
}
EXPECTED_MANIFEST_INPUTS = {
    "data/samples/lte-demo/performance.csv": (
        "/opt/networkagent/data/samples/lte-demo/performance.csv"
    ),
    "data/samples/lte-demo/safe-cell-traces.csv": (
        "/opt/networkagent/data/samples/lte-demo/safe-cell-traces.csv"
    ),
    "data/rca-rules/lte/5g-sa-bubbleran-persistent-interference-ul-bler.json": (
        "/opt/networkagent/data/rca-rules/lte/5g-sa-bubbleran-persistent-interference-ul-bler.json"
    ),
    "data/rca-rules/lte/erab-security-setup.json": (
        "/opt/networkagent/data/rca-rules/lte/erab-security-setup.json"
    ),
    "data/rca-rules/lte/retainability-uplink-rssi.json": (
        "/opt/networkagent/data/rca-rules/lte/retainability-uplink-rssi.json"
    ),
    "data/docs/lte/telco-lte-fields-guide.zh-CN.md": (
        "/opt/networkagent/data/docs/lte/telco-lte-fields-guide.zh-CN.md"
    ),
}
EXPECTED_MANIFEST_ROOTS = {
    "/opt/networkagent/data/rca-rules/lte",
    "/opt/networkagent/data/docs/lte",
}
EXPECTED_MOUNT_TARGETS = {
    "assurance": {WORKSPACE_TARGET, *INPUT_TARGETS},
    "init": {WORKSPACE_TARGET, *INPUT_TARGETS},
    "reset": {WORKSPACE_TARGET},
    "probe": set(),
    "smoke": set(INPUT_TARGETS),
}
FORBIDDEN_SERVICE_KEYS = {
    "ports",
    "expose",
    "networks",
    "network_mode_ipv4_address",
    "network_mode_ipv6_address",
    "privileged",
    "devices",
    "device_cgroup_rules",
    "cap_add",
    "env_file",
    "environment",
    "secrets",
    "configs",
    "extra_hosts",
    "dns",
    "dns_search",
    "dns_opt",
    "links",
    "external_links",
    "pid",
    "ipc",
    "cgroup",
    "cgroup_parent",
    "userns_mode",
    "entrypoint",
    "stdin_open",
    "tty",
}
ALLOWED_TOP_LEVEL_KEYS = {
    "name",
    "services",
    "volumes",
    "networks",
    "secrets",
    "configs",
}
ALLOWED_SERVICE_KEYS = {
    "image",
    "platform",
    "pull_policy",
    "build",
    "command",
    "network_mode",
    "user",
    "read_only",
    "cap_drop",
    "security_opt",
    "pids_limit",
    "mem_limit",
    "cpus",
    "ulimits",
    "tmpfs",
    "init",
    "restart",
    "volumes",
    "profiles",
    "depends_on",
    "healthcheck",
} | FORBIDDEN_SERVICE_KEYS
ALLOWED_MOUNT_KEYS = {
    "type",
    "source",
    "target",
    "read_only",
    "bind",
    "volume",
}
PINNED_BASE = (
    "python:3.12-slim-bookworm@sha256:"
    "0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579"
)
EXPECTED_COMPOSE_SHA256 = (
    "279494f4845b7733d9cf4eb453b32e56b03342aec93968ba59736cf692f43f51"
)
MAX_COMPOSE_SOURCE_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PolicyViolation(RuntimeError):
    """The resolved deployment is outside the approved local threat model."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PolicyViolation(f"{label} must be an object")
    return value


def _nonempty(value: object) -> bool:
    return value not in (None, False, "", [], {})


def _byte_value(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PolicyViolation(f"{label} is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise PolicyViolation(f"{label} is invalid")
        return int(value)
    if not isinstance(value, str):
        raise PolicyViolation(f"{label} is invalid")
    normalized = value.strip().lower()
    if normalized.isdigit():
        return int(normalized)
    match = re.fullmatch(r"([0-9]+)([kmgt])(?:i?b?)?", normalized)
    if match is None:
        raise PolicyViolation(f"{label} is invalid")
    factors = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    return int(match.group(1)) * factors[match.group(2)]


def _validate_service_baseline(name: str, service: Mapping[str, object]) -> None:
    unknown = set(service) - ALLOWED_SERVICE_KEYS
    if unknown:
        key = sorted(unknown)[0]
        raise PolicyViolation(f"service {name} forbids unsupported key {key}")
    for key in FORBIDDEN_SERVICE_KEYS:
        if key in service and _nonempty(service[key]):
            raise PolicyViolation(f"service {name} forbids {key}")
    if service.get("network_mode") != EXPECTED_NETWORK_MODES[name]:
        raise PolicyViolation(f"service {name} network_mode is not isolated")
    if service.get("platform") != EXPECTED_PLATFORM:
        raise PolicyViolation(f"service {name} platform must be linux/amd64")
    if service.get("command") != EXPECTED_COMMANDS[name]:
        raise PolicyViolation(f"service {name} command is not closed")
    if service.get("user") != "10001:10001":
        raise PolicyViolation(f"service {name} user must be 10001:10001")
    if service.get("read_only") is not True:
        raise PolicyViolation(f"service {name} read_only root is required")
    cap_drop = service.get("cap_drop")
    if not isinstance(cap_drop, list) or {str(item).upper() for item in cap_drop} != {
        "ALL"
    }:
        raise PolicyViolation(f"service {name} cap_drop must contain only ALL")
    options = service.get("security_opt")
    if not isinstance(options, list) or set(options) != {"no-new-privileges:true"}:
        raise PolicyViolation(f"service {name} security_opt is incomplete")
    pids = service.get("pids_limit")
    if not isinstance(pids, int) or isinstance(pids, bool) or not 1 <= pids <= 128:
        raise PolicyViolation(f"service {name} pids_limit is invalid")
    if (
        not 1
        <= _byte_value(service.get("mem_limit"), f"service {name} mem_limit")
        <= 805_306_368
    ):
        raise PolicyViolation(f"service {name} mem_limit exceeds policy")
    cpus = service.get("cpus")
    if isinstance(cpus, bool):
        raise PolicyViolation(f"service {name} cpus is invalid")
    try:
        cpus_value = float(cpus)
    except (TypeError, ValueError):
        raise PolicyViolation(f"service {name} cpus is invalid") from None
    if not 0 < cpus_value <= 1:
        raise PolicyViolation(f"service {name} cpus exceeds policy")
    ulimits = _mapping(service.get("ulimits"), f"service {name} ulimits")
    if set(ulimits) != {"nofile"}:
        raise PolicyViolation(f"service {name} ulimits contains unsupported limits")
    nofile = _mapping(ulimits.get("nofile"), f"service {name} nofile")
    if set(nofile) != {"soft", "hard"}:
        raise PolicyViolation(f"service {name} nofile contains unsupported keys")
    if nofile.get("soft") != 1024 or nofile.get("hard") != 1024:
        raise PolicyViolation(f"service {name} nofile is invalid")
    tmpfs = service.get("tmpfs")
    if not isinstance(tmpfs, list) or len(tmpfs) != 1 or not isinstance(tmpfs[0], str):
        raise PolicyViolation(f"service {name} tmpfs is invalid")
    tmpfs_value = tmpfs[0].lower()
    if not tmpfs_value.startswith("/tmp:"):
        raise PolicyViolation(f"service {name} tmpfs hardening is incomplete")
    tmpfs_options = tmpfs_value.split(":", 1)[1].split(",")
    expected_tmpfs_options = {
        "rw",
        "noexec",
        "nosuid",
        "nodev",
        "mode=1777",
        "size=67108864",
    }
    if (
        len(tmpfs_options) != len(expected_tmpfs_options)
        or set(tmpfs_options) != expected_tmpfs_options
    ):
        raise PolicyViolation(f"service {name} tmpfs hardening is incomplete")
    if (
        service.get("init") is not True
        or str(service.get("restart", "")).lower() != "no"
    ):
        raise PolicyViolation(f"service {name} lifecycle hardening is incomplete")


def _normalized_source(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyViolation("mount source is invalid")
    return value.replace("\\", "/").rstrip("/")


def _validate_mounts(name: str, service: Mapping[str, object]) -> None:
    raw_mounts = service.get("volumes") or []
    if not isinstance(raw_mounts, list):
        raise PolicyViolation(f"service {name} volumes must use long syntax")
    mounts: dict[str, Mapping[str, object]] = {}
    for raw in raw_mounts:
        mount = _mapping(raw, f"service {name} volume")
        unknown = set(mount) - ALLOWED_MOUNT_KEYS
        if unknown:
            raise PolicyViolation(
                f"service {name} mount forbids unsupported key {sorted(unknown)[0]}"
            )
        target = mount.get("target")
        if not isinstance(target, str) or target in mounts:
            raise PolicyViolation(f"service {name} mount target is invalid")
        mounts[target] = mount
    if set(mounts) != EXPECTED_MOUNT_TARGETS[name]:
        raise PolicyViolation(f"service {name} mount targets violate policy")

    workspace = mounts.get(WORKSPACE_TARGET)
    if workspace is not None:
        source = _normalized_source(workspace.get("source"))
        if workspace.get("type") != "volume" or not source.endswith(
            "networkagent_workspace"
        ):
            raise PolicyViolation(f"service {name} workspace must be the named volume")
        if workspace.get("read_only") not in (None, False):
            raise PolicyViolation(f"service {name} workspace must be writable")
        volume_options = workspace.get("volume")
        if volume_options is not None and _nonempty(volume_options):
            raise PolicyViolation(
                f"service {name} workspace volume options are forbidden"
            )

    for target, suffix in INPUT_TARGETS.items():
        mount = mounts.get(target)
        if mount is None:
            continue
        if mount.get("type") != "bind" or mount.get("read_only") is not True:
            raise PolicyViolation(f"service {name} input bind must be read-only")
        source = _normalized_source(mount.get("source"))
        if not source.endswith("/" + suffix):
            raise PolicyViolation(f"service {name} input bind source is unexpected")
        bind = _mapping(mount.get("bind"), f"service {name} bind")
        if set(bind) - {
            "create_host_path",
            "propagation",
            "recursive",
            "selinux",
        }:
            raise PolicyViolation(f"service {name} bind options are not exact")
        # Compose 2.38.x omits an explicit false create_host_path value from
        # `config --format json`. The source file is independently pinned by
        # validate_source_compose(), so an omitted resolved value is safe here.
        if "create_host_path" in bind and bind.get("create_host_path") is not False:
            raise PolicyViolation(f"service {name} bind create_host_path must be false")
        if bind.get("propagation") not in (None, "", "rprivate"):
            raise PolicyViolation(f"service {name} bind propagation must be rprivate")
        if bind.get("recursive") not in (None, "", "enabled"):
            raise PolicyViolation(f"service {name} bind recursion must stay enabled")
        if bind.get("selinux") not in (None, ""):
            raise PolicyViolation(
                f"service {name} bind SELinux relabeling is forbidden"
            )


def _validate_profiles_and_health(name: str, service: Mapping[str, object]) -> None:
    expected_profile = EXPECTED_PROFILES.get(name)
    if expected_profile is None:
        if _nonempty(service.get("profiles")):
            raise PolicyViolation("assurance must be enabled without a profile")
    elif service.get("profiles") != expected_profile:
        raise PolicyViolation(f"service {name} profile is invalid")
    if name in {"probe", "smoke"}:
        dependencies = _mapping(service.get("depends_on"), f"service {name} depends_on")
        assurance = _mapping(
            dependencies.get("assurance"), f"service {name} assurance dependency"
        )
        if set(dependencies) != {"assurance"} or set(assurance) - {
            "condition",
            "required",
            "restart",
        }:
            raise PolicyViolation(f"service {name} dependency is not exact")
        if assurance.get("condition") != "service_healthy":
            raise PolicyViolation(f"service {name} must wait for healthy assurance")
    health = service.get("healthcheck")
    if name != "assurance":
        if _nonempty(health):
            raise PolicyViolation(f"service {name} must not replace the healthcheck")
        return
    health_mapping = _mapping(health, "assurance healthcheck")
    if set(health_mapping) - {
        "test",
        "interval",
        "timeout",
        "retries",
        "start_period",
        "disable",
    }:
        raise PolicyViolation("assurance healthcheck contains unsupported keys")
    if health_mapping.get("test") != [
        "CMD",
        "python",
        "/opt/networkagent/bin/container_entrypoint.py",
        "probe",
    ]:
        raise PolicyViolation("assurance healthcheck must call the local healthz probe")
    if health_mapping.get("disable") is True:
        raise PolicyViolation("assurance healthcheck cannot be disabled")


def _validate_build(
    service: Mapping[str, object], repository_root: Path | None
) -> None:
    build = _mapping(service.get("build"), "assurance build")
    if set(build) != {"context", "dockerfile"}:
        raise PolicyViolation("assurance build schema is not exact")
    context = build.get("context")
    dockerfile = build.get("dockerfile")
    if not isinstance(context, str) or not Path(context).is_absolute():
        raise PolicyViolation("assurance build context must be absolute")
    if repository_root is not None:
        try:
            if not Path(context).resolve().samefile(repository_root):
                raise PolicyViolation(
                    "assurance build context is not the repository root"
                )
        except OSError:
            raise PolicyViolation(
                "assurance build context is not the repository root"
            ) from None
    if not isinstance(dockerfile, str):
        raise PolicyViolation("assurance Dockerfile path is invalid")
    dockerfile_path = Path(dockerfile)
    if dockerfile_path.is_absolute():
        expected = Path(context) / "deploy" / "local" / "Dockerfile"
        if dockerfile_path.resolve() != expected.resolve():
            raise PolicyViolation("assurance Dockerfile path is not exact")
    elif dockerfile_path.as_posix() != "deploy/local/Dockerfile":
        raise PolicyViolation("assurance Dockerfile path is not exact")


def validate_compose_config(
    config: Mapping[str, object], *, repository_root: Path | None = None
) -> None:
    """Validate Docker Compose's fully merged JSON representation."""

    unknown_top_level = set(config) - ALLOWED_TOP_LEVEL_KEYS
    if unknown_top_level:
        raise PolicyViolation(
            f"top-level key {sorted(unknown_top_level)[0]} is unsupported"
        )
    if config.get("name") != "networkagent-local":
        raise PolicyViolation("Compose project name is not exact")
    if _nonempty(config.get("networks")):
        raise PolicyViolation("top-level networks are forbidden")
    if _nonempty(config.get("secrets")) or _nonempty(config.get("configs")):
        raise PolicyViolation("top-level secrets/configs are forbidden")
    services = _mapping(config.get("services"), "services")
    if set(services) != EXPECTED_SERVICES:
        raise PolicyViolation("resolved service set is not exact")
    volumes = _mapping(config.get("volumes"), "volumes")
    if set(volumes) != {"networkagent_workspace"}:
        raise PolicyViolation("only the workspace named volume is allowed")
    volume = _mapping(volumes["networkagent_workspace"], "workspace volume")
    if set(volume) - {"name", "external", "driver", "driver_opts", "labels"}:
        raise PolicyViolation("workspace volume contains unsupported keys")
    if _nonempty(volume.get("external")) or _nonempty(volume.get("driver_opts")):
        raise PolicyViolation("workspace volume cannot be external or customized")
    if volume.get("driver") not in (None, "", "local") or _nonempty(
        volume.get("labels")
    ):
        raise PolicyViolation("workspace volume must use the unlabelled local driver")
    volume_name = volume.get("name")
    if volume_name is not None and (
        not isinstance(volume_name, str)
        or not volume_name.endswith("networkagent_workspace")
    ):
        raise PolicyViolation("workspace volume name is not exact")

    images: set[str] = set()
    for name in sorted(EXPECTED_SERVICES):
        service = _mapping(services[name], f"service {name}")
        image = service.get("image")
        if image != EXPECTED_IMAGE:
            raise PolicyViolation(
                f"service {name} image tag is not the fixed candidate"
            )
        if service.get("pull_policy") != "never":
            raise PolicyViolation(f"service {name} pull_policy must be never")
        build = service.get("build")
        if name == "assurance":
            _validate_build(service, repository_root)
        elif _nonempty(build):
            raise PolicyViolation(f"service {name} must reuse the assurance build")
        images.add(image)
        _validate_service_baseline(name, service)
        _validate_mounts(name, service)
        _validate_profiles_and_health(name, service)
    if len(images) != 1:
        raise PolicyViolation("all services must use one immutable build result")


def _load_bounded_json(path: Path | None) -> Mapping[str, object]:
    maximum = 2 * 1024 * 1024
    try:
        if path is None:
            raw = sys.stdin.buffer.read(maximum + 1)
        else:
            with path.open("rb") as handle:
                raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise PolicyViolation("resolved JSON exceeds byte limit")
        value = json.loads(raw.decode("utf-8"))
    except PolicyViolation:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PolicyViolation("resolved JSON is unreadable") from None
    return _mapping(value, "resolved JSON")


def _hash_regular(path: Path, expected_bytes: int) -> str:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_bytes:
            raise PolicyViolation(f"manifest bytes mismatch: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(65_536):
                digest.update(chunk)
        return digest.hexdigest()
    except PolicyViolation:
        raise
    except OSError:
        raise PolicyViolation(f"manifest source is unreadable: {path.name}") from None


def validate_source_manifest(repository_root: Path, manifest_path: Path) -> None:
    """Verify that the committed image manifest matches the source inputs."""

    try:
        raw = manifest_path.read_bytes()
        if len(raw) > 65_536:
            raise PolicyViolation("input manifest exceeds byte limit")
        manifest = json.loads(raw.decode("utf-8"))
    except PolicyViolation:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PolicyViolation("input manifest is unreadable") from None
    value = _mapping(manifest, "input manifest")
    if value.get("schema_version") != "1.0" or value.get("algorithm") != "sha256":
        raise PolicyViolation("input manifest schema is invalid")
    files = value.get("files")
    max_files = value.get("max_files")
    max_total = value.get("max_total_bytes")
    if (
        not isinstance(files, list)
        or not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 1 <= len(files) <= max_files <= 64
        or not isinstance(max_total, int)
        or isinstance(max_total, bool)
        or not 1 <= max_total <= 16 * 1024 * 1024
    ):
        raise PolicyViolation("input manifest limits are invalid")
    total = 0
    seen_sources: set[str] = set()
    container_paths: set[str] = set()
    for raw_entry in files:
        entry = _mapping(raw_entry, "manifest entry")
        source = entry.get("source")
        container_path = entry.get("container_path")
        expected_bytes = entry.get("bytes")
        expected_hash = entry.get("sha256")
        if (
            not isinstance(source, str)
            or not source
            or Path(source).is_absolute()
            or ".." in Path(source).parts
            or source in seen_sources
        ):
            raise PolicyViolation("manifest source is invalid")
        if (
            not isinstance(container_path, str)
            or not container_path.startswith("/opt/networkagent/data/")
            or container_path in container_paths
        ):
            raise PolicyViolation("manifest container path is invalid")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 0
        ):
            raise PolicyViolation("manifest bytes value is invalid")
        if (
            not isinstance(expected_hash, str)
            or _SHA256.fullmatch(expected_hash) is None
        ):
            raise PolicyViolation("manifest sha256 value is invalid")
        source_path = repository_root / Path(source)
        actual_hash = _hash_regular(source_path, expected_bytes)
        if actual_hash != expected_hash:
            raise PolicyViolation(f"manifest sha256 mismatch: {source_path.name}")
        total += expected_bytes
        if total > max_total:
            raise PolicyViolation("manifest total bytes exceed limit")
        seen_sources.add(source)
        container_paths.add(container_path)
    if manifest_path.name == "input-manifest.json":
        actual_mapping = {
            str(_mapping(entry, "manifest entry")["source"]): str(
                _mapping(entry, "manifest entry")["container_path"]
            )
            for entry in files
        }
        if actual_mapping != EXPECTED_MANIFEST_INPUTS:
            raise PolicyViolation("input manifest file set is not exact")
        roots = value.get("directory_roots")
        if not isinstance(roots, list) or set(roots) != EXPECTED_MANIFEST_ROOTS:
            raise PolicyViolation("input manifest directory roots are not exact")


def validate_repository_artifacts(repository_root: Path) -> None:
    compose = repository_root / "deploy" / "local" / "compose.yaml"
    dockerfile = repository_root / "deploy" / "local" / "Dockerfile"
    dockerignore = repository_root / "deploy" / "local" / "Dockerfile.dockerignore"
    manifest = repository_root / "deploy" / "local" / "input-manifest.json"
    validate_source_compose(compose)
    _validate_source_policy_file(
        dockerfile,
        expected_sha256=EXPECTED_DOCKERFILE_SHA256,
        label="Dockerfile",
    )
    _validate_source_policy_file(
        dockerignore,
        expected_sha256=EXPECTED_DOCKERIGNORE_SHA256,
        label="Dockerfile.dockerignore",
    )
    try:
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        ignore_text = dockerignore.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise PolicyViolation("container build policy files are unavailable") from None
    from_lines = [
        line.strip()
        for line in dockerfile_text.splitlines()
        if line.strip().startswith("FROM ")
    ]
    if len(from_lines) != 2 or any(PINNED_BASE not in line for line in from_lines):
        raise PolicyViolation("Dockerfile base image digest is not exact")
    runtime_text = dockerfile_text.split(from_lines[1], 1)[1]
    runtime_copy_lines = {
        line.strip()
        for line in runtime_text.splitlines()
        if line.strip().startswith("COPY ")
    }
    expected_runtime_copies = {
        "COPY --chown=10001:10001 tools/local-container/container_entrypoint.py /opt/networkagent/bin/container_entrypoint.py",
        "COPY --chown=10001:10001 tools/local-stack/local_stack.py /opt/networkagent/tools/local-stack/local_stack.py",
        "COPY --chown=10001:10001 deploy/local/input-manifest.json /opt/networkagent/share/input-manifest.json",
    }
    if runtime_copy_lines != expected_runtime_copies:
        raise PolicyViolation("Dockerfile runtime COPY allowlist is not exact")
    build_lock_mount = (
        "source=deploy/local/build-requirements-py312-linux-amd64.lock,"
        "target=/build-requirements.lock,ro"
    )
    runtime_lock_mount = (
        "source=deploy/local/runtime-requirements-py312-linux-amd64.lock,"
        "target=/runtime-requirements.lock,ro"
    )
    if (
        build_lock_mount not in dockerfile_text
        or dockerfile_text.count(runtime_lock_mount) != 2
        or "RUN --network=none" not in runtime_text
        or "--mount=type=bind,from=wheel-builder,source=/wheels,target=/wheels,ro"
        not in runtime_text
        or "--require-hashes" not in dockerfile_text
        or "--only-binary=:all:" not in dockerfile_text
        or "--no-index" not in runtime_text
        or "--no-deps" not in runtime_text
        or "--no-compile" not in runtime_text
        or "runtime-constraints.txt" in dockerfile_text
        or "--constraint" in dockerfile_text
    ):
        raise PolicyViolation("Dockerfile transient build mounts are not exact")
    if "PYTHONDONTWRITEBYTECODE=1" not in runtime_text:
        raise PolicyViolation("Dockerfile runtime bytecode cache control is missing")
    if (
        'org.opencontainers.image.source="https://github.com/LeegenSteven/NetworkAgent-dev"'
        not in runtime_text
    ):
        raise PolicyViolation("Dockerfile OCI source does not match the repository")
    if "USER 10001:10001" not in dockerfile_text:
        raise PolicyViolation("Dockerfile numeric runtime user is missing")
    if (
        'ENTRYPOINT ["python", "/opt/networkagent/bin/container_entrypoint.py"]'
        not in dockerfile_text
    ):
        raise PolicyViolation("Dockerfile entrypoint must use JSON exec form")
    for pattern in (
        r"(?im)^\s*COPY\s+.*\bdata[/\\]",
        r"(?im)^\s*COPY\s+.*\btests?[/\\]",
    ):
        if re.search(pattern, dockerfile_text):
            raise PolicyViolation("Dockerfile must not copy raw data or tests")
    required_ignores = {".git", "data", "**/tests", "**/__pycache__", ".local"}
    actual_ignores = {
        line.strip()
        for line in ignore_text.splitlines()
        if line.strip() and not line.startswith("#")
    }
    if not required_ignores.issubset(actual_ignores):
        raise PolicyViolation("Dockerfile.dockerignore is incomplete")
    expected_lock_includes = {
        "!deploy/local/build-requirements-py312-linux-amd64.lock",
        "!deploy/local/runtime-requirements-py312-linux-amd64.lock",
    }
    if not expected_lock_includes.issubset(actual_ignores) or any(
        "runtime-constraints.txt" in line for line in actual_ignores
    ):
        raise PolicyViolation("Dockerfile.dockerignore lock allowlist is incomplete")
    validate_source_manifest(repository_root, manifest)


def _validate_source_policy_file(
    path: Path, *, expected_sha256: str, label: str
) -> None:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or not 0 < metadata.st_size <= 256 * 1024
        ):
            raise PolicyViolation(f"{label} source file is unsafe")
        source = path.read_text(encoding="utf-8")
    except PolicyViolation:
        raise
    except (OSError, UnicodeError):
        raise PolicyViolation(f"{label} source file is unavailable") from None
    normalized = source.replace("\r\n", "\n")
    if "\r" in normalized or "\x00" in normalized:
        raise PolicyViolation(f"{label} source encoding is invalid")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if digest != expected_sha256:
        raise PolicyViolation(f"{label} policy digest does not match")


def validate_source_compose(compose_path: Path) -> None:
    """Pin the source contract that normalized Compose JSON cannot preserve."""

    try:
        metadata = compose_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or compose_path.is_symlink()
            or metadata.st_size > MAX_COMPOSE_SOURCE_BYTES
            or metadata.st_size <= 0
        ):
            raise PolicyViolation("source Compose file is unsafe")
        source = compose_path.read_text(encoding="utf-8")
    except PolicyViolation:
        raise
    except (OSError, UnicodeError):
        raise PolicyViolation("source Compose file is unavailable") from None
    normalized = source.replace("\r\n", "\n")
    if "\r" in normalized:
        raise PolicyViolation("source Compose line endings are invalid")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if digest != EXPECTED_COMPOSE_SHA256:
        raise PolicyViolation("source Compose policy digest does not match")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="networkagent-compose-guard")
    parser.add_argument(
        "--resolved-json",
        required=True,
        help="docker compose config --format json output, or '-' for stdin",
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    path = None if arguments.resolved_json == "-" else Path(arguments.resolved_json)
    repository_root = (
        arguments.repository_root.resolve()
        if arguments.repository_root is not None
        else None
    )
    validate_compose_config(_load_bounded_json(path), repository_root=repository_root)
    if repository_root is not None:
        validate_repository_artifacts(repository_root)
    print(
        json.dumps({"ok": True, "policy": "local-container-v1"}, separators=(",", ":"))
    )
    return 0


def _run() -> int:
    try:
        return main()
    except PolicyViolation as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": "POLICY_VIOLATION", "message": str(exc)},
                },
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_run())
