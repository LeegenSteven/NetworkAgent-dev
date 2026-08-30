from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "compose_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("compose_guard", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mount(
    mount_type: str,
    source: str,
    target: str,
    *,
    read_only: bool,
) -> dict[str, object]:
    value: dict[str, object] = {
        "type": mount_type,
        "source": source,
        "target": target,
        "read_only": read_only,
    }
    if mount_type == "bind":
        value["bind"] = {"create_host_path": False}
    return value


def _service(
    command: str,
    network_mode: str,
    mounts: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "image": "networkagent-local:dev",
        "pull_policy": "never",
        "command": [command],
        "network_mode": network_mode,
        "user": "10001:10001",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 128,
        "mem_limit": "805306368",
        "cpus": 1.0,
        "ulimits": {"nofile": {"soft": 1024, "hard": 1024}},
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777"],
        "init": True,
        "restart": "no",
        "volumes": mounts,
    }


def _secure_config() -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[3]
    workspace_rw = _mount(
        "volume",
        "networkagent_workspace",
        "/var/lib/networkagent",
        read_only=False,
    )
    inputs = [
        _mount(
            "bind",
            "/repo/data/samples/lte-demo/performance.csv",
            "/opt/networkagent/data/samples/lte-demo/performance.csv",
            read_only=True,
        ),
        _mount(
            "bind",
            "/repo/data/samples/lte-demo/safe-cell-traces.csv",
            "/opt/networkagent/data/samples/lte-demo/safe-cell-traces.csv",
            read_only=True,
        ),
        _mount(
            "bind",
            "/repo/data/rca-rules/lte",
            "/opt/networkagent/data/rca-rules/lte",
            read_only=True,
        ),
        _mount(
            "bind",
            "/repo/data/docs/lte",
            "/opt/networkagent/data/docs/lte",
            read_only=True,
        ),
    ]
    assurance = _service("serve", "none", [workspace_rw, *inputs])
    assurance["build"] = {
        "context": str(repository_root),
        "dockerfile": str(repository_root / "deploy" / "local" / "Dockerfile"),
    }
    assurance["healthcheck"] = {
        "test": [
            "CMD",
            "python",
            "/opt/networkagent/bin/container_entrypoint.py",
            "probe",
        ],
        "interval": "10s",
        "timeout": "3s",
        "retries": 6,
        "start_period": "10s",
    }
    init = _service("init", "none", [workspace_rw, *inputs])
    init["profiles"] = ["ops"]
    reset = _service("reset", "none", [workspace_rw])
    reset["profiles"] = ["ops"]
    probe = _service("probe", "service:assurance", [])
    probe["profiles"] = ["diagnostics"]
    probe["depends_on"] = {"assurance": {"condition": "service_healthy"}}
    smoke = _service("smoke", "service:assurance", inputs)
    smoke["profiles"] = ["test"]
    smoke["depends_on"] = {"assurance": {"condition": "service_healthy"}}
    return {
        "name": "networkagent-local",
        "services": {
            "assurance": assurance,
            "init": init,
            "reset": reset,
            "probe": probe,
            "smoke": smoke,
        },
        "volumes": {"networkagent_workspace": {}},
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cpus", True),
        ("mem_limit", True),
        ("mem_limit", 1.1),
    ],
)
def test_boolean_or_fractional_resource_limits_are_rejected(
    key: str, value: object
) -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"][key] = value
    with pytest.raises(module.PolicyViolation, match=key):
        module.validate_compose_config(config)


def test_arbitrary_image_name_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["smoke"]["image"] = "registry.example/unreviewed:dev"
    with pytest.raises(module.PolicyViolation, match="fixed candidate"):
        module.validate_compose_config(config)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("volumes_from", ["host-with-docker-sock:rw"]),
        ("use_api_socket", True),
        ("post_start", [{"command": ["python", "-c", "raise SystemExit()"]}]),
        ("pre_stop", [{"command": ["python", "-c", "raise SystemExit()"]}]),
        ("group_add", ["0"]),
        ("gpus", "all"),
        ("runtime", "unreviewed-runtime"),
    ],
)
def test_unapproved_compose_capability_is_rejected(key: str, value: object) -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"][key] = value
    with pytest.raises(module.PolicyViolation, match=key):
        module.validate_compose_config(config)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("secrets", ["build-key"]),
        ("ssh", ["default"]),
        ("network", "host"),
        ("privileged", True),
        ("entitlements", ["network.host"]),
        ("additional_contexts", {"payload": "/unreviewed"}),
    ],
)
def test_unapproved_build_capability_is_rejected(key: str, value: object) -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"]["build"][key] = value
    with pytest.raises(module.PolicyViolation, match="build schema"):
        module.validate_compose_config(config)


def test_build_context_must_equal_repository_root(tmp_path: Path) -> None:
    module = _load_module()
    config = _secure_config()
    with pytest.raises(module.PolicyViolation, match="repository root"):
        module.validate_compose_config(config, repository_root=tmp_path)


def test_secure_resolved_compose_config_is_accepted() -> None:
    module = _load_module()
    module.validate_compose_config(_secure_config())


def test_resolved_compose_extension_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["x-runtime"] = {"read_only": True}
    with pytest.raises(module.PolicyViolation, match="top-level key x-runtime"):
        module.validate_compose_config(config)


def test_empty_resolved_defaults_are_accepted_but_nonempty_entrypoint_is_not() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"].update(
        {
            "entrypoint": None,
            "environment": {},
            "ports": [],
            "expose": [],
            "networks": None,
        }
    )
    config["services"]["probe"]["volumes"] = None
    module.validate_compose_config(config)

    config["services"]["assurance"]["entrypoint"] = ["python", "-c", "pass"]
    with pytest.raises(module.PolicyViolation, match="entrypoint"):
        module.validate_compose_config(config)


@pytest.mark.parametrize(
    ("service", "key", "value", "expected"),
    [
        ("assurance", "ports", ["8085:8085"], "ports"),
        ("assurance", "expose", ["8085"], "expose"),
        ("assurance", "network_mode", "bridge", "network_mode"),
        ("assurance", "privileged", True, "privileged"),
        ("assurance", "devices", ["/dev/net/tun"], "devices"),
        ("assurance", "env_file", [".env"], "env_file"),
        ("assurance", "user", "0:0", "user"),
        ("assurance", "read_only", False, "read_only"),
        ("assurance", "cap_add", ["NET_ADMIN"], "cap_add"),
        ("assurance", "pid", "host", "pid"),
        ("assurance", "ipc", "host", "ipc"),
        ("smoke", "network_mode", "host", "network_mode"),
    ],
)
def test_forbidden_service_settings_fail_closed(
    service: str,
    key: str,
    value: object,
    expected: str,
) -> None:
    module = _load_module()
    config = _secure_config()
    config["services"][service][key] = value
    with pytest.raises(module.PolicyViolation, match=expected):
        module.validate_compose_config(config)


def test_default_network_definition_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["networks"] = {"default": {}}
    with pytest.raises(module.PolicyViolation, match="networks"):
        module.validate_compose_config(config)


def test_contradictory_tmpfs_options_are_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"]["tmpfs"] = [
        "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777,exec,suid,dev"
    ]
    with pytest.raises(module.PolicyViolation, match="tmpfs hardening"):
        module.validate_compose_config(config)


def test_writable_input_bind_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"]["volumes"][1]["read_only"] = False
    with pytest.raises(module.PolicyViolation, match="read-only"):
        module.validate_compose_config(config)


def test_bind_that_can_create_host_path_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["init"]["volumes"][2]["bind"]["create_host_path"] = True
    with pytest.raises(module.PolicyViolation, match="create_host_path"):
        module.validate_compose_config(config)


def test_docker_socket_mount_is_rejected() -> None:
    module = _load_module()
    config = _secure_config()
    config["services"]["assurance"]["volumes"].append(
        _mount("bind", "/var/run/docker.sock", "/var/run/docker.sock", read_only=True)
    )
    with pytest.raises(module.PolicyViolation, match="mount targets"):
        module.validate_compose_config(config)


def test_environment_and_secret_channels_are_rejected() -> None:
    module = _load_module()
    for key, value in (
        ("environment", {"GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json"}),
        ("secrets", ["gcp-key"]),
        ("configs", ["runtime-config"]),
    ):
        config = _secure_config()
        config["services"]["assurance"][key] = value
        with pytest.raises(module.PolicyViolation, match=key):
            module.validate_compose_config(config)


def test_manifest_matches_repository_inputs(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "data" / "sample.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"bounded fixture\n")
    manifest = {
        "schema_version": "1.0",
        "algorithm": "sha256",
        "max_files": 4,
        "max_total_bytes": 1024,
        "files": [
            {
                "source": "data/sample.txt",
                "container_path": "/opt/networkagent/data/sample.txt",
                "bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "directory_roots": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    module.validate_source_manifest(tmp_path, path)


def test_manifest_hash_drift_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "data" / "sample.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"changed\n")
    manifest = {
        "schema_version": "1.0",
        "algorithm": "sha256",
        "max_files": 4,
        "max_total_bytes": 1024,
        "files": [
            {
                "source": "data/sample.txt",
                "container_path": "/opt/networkagent/data/sample.txt",
                "bytes": source.stat().st_size,
                "sha256": "0" * 64,
            }
        ],
        "directory_roots": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(module.PolicyViolation, match="sha256"):
        module.validate_source_manifest(tmp_path, path)


def test_cli_accepts_resolved_json(tmp_path: Path) -> None:
    module = _load_module()
    config_path = tmp_path / "compose.json"
    config_path.write_text(json.dumps(_secure_config()), encoding="utf-8")
    assert module.main(["--resolved-json", str(config_path)]) == 0


def test_unknown_service_is_rejected() -> None:
    module = _load_module()
    config = copy.deepcopy(_secure_config())
    config["services"]["proxy"] = _service("probe", "none", [])
    with pytest.raises(module.PolicyViolation, match="service set"):
        module.validate_compose_config(config)
