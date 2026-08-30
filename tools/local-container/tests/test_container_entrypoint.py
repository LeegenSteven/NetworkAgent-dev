from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from email.message import Message
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "container_entrypoint.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("container_entrypoint", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_for(root: Path, files: dict[str, bytes]) -> Path:
    entries = []
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entries.append(
            {
                "source": relative,
                "container_path": str(target),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "algorithm": "sha256",
        "max_files": 8,
        "max_total_bytes": 4096,
        "files": entries,
        "directory_roots": [str(root / "rules")],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class _HttpResponse:
    def __init__(
        self,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self.body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(self.body))

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, maximum: int) -> bytes:
        return self.body[:maximum]


class _HttpOpener:
    def __init__(self, response: _HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[object, float]] = []

    def open(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        return self.response


class _SequenceHttpOpener:
    def __init__(self, responses: list[_HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, float]] = []

    def open(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        return self.responses.pop(0)


def _governance_payload(
    incident_id: str,
    *,
    status: str,
    revision: int,
    action_hash: str = "a" * 64,
    approval_status: str = "PENDING",
    verification_status: str | None = None,
    replayed: bool = False,
) -> dict[str, object]:
    return {
        "ok": True,
        "data": {
            "incident": {
                "incident_id": incident_id,
                "status": status,
                "revision": revision,
            },
            "rca": {"conclusion": "CONCLUSIVE"},
            "action": {
                "action_type": "LOCAL_SIMULATION",
                "action_hash": action_hash,
            },
            "approval": {
                "status": approval_status,
                "action_hash": action_hash,
            },
            "action_runs": [],
            "verification": (
                {"status": verification_status}
                if verification_status is not None
                else None
            ),
            "replayed": replayed,
        },
    }


def _install_http_response(monkeypatch, module, payload: object) -> _HttpOpener:
    opener = _HttpOpener(_HttpResponse(payload))
    handlers: list[object] = []

    def fake_build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return opener

    monkeypatch.setattr(module, "build_opener", fake_build_opener)
    monkeypatch.setattr(module, "validate_inputs", lambda: {"files": 6, "bytes": 1})
    opener.handlers = handlers
    return opener


def test_bounded_manifest_validation_accepts_exact_inputs(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(
        tmp_path,
        {
            "performance.csv": b"time,kpi\n0,1\n",
            "rules/rule.json": b'{"rule":"safe"}\n',
        },
    )
    result = module.validate_inputs(manifest)
    assert result == {"files": 2, "bytes": 29}


def test_manifest_rejects_modified_input(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    (tmp_path / "performance.csv").write_bytes(b"tampered\n")
    with pytest.raises(module.InputValidationError, match="size mismatch"):
        module.validate_inputs(manifest)


def test_manifest_rejects_extra_file_below_controlled_directory(
    tmp_path: Path,
) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"rules/rule.json": b"{}\n"})
    (tmp_path / "rules" / "unreviewed.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(module.InputValidationError, match="directory contents"):
        module.validate_inputs(manifest)


def test_manifest_rejects_excess_file_count(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["max_files"] = 0
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.InputValidationError, match="file count"):
        module.validate_inputs(manifest)


def test_manifest_rejects_symlinked_file_when_supported(tmp_path: Path) -> None:
    module = _load_module()
    manifest = _manifest_for(tmp_path, {"performance.csv": b"safe\n"})
    source = tmp_path / "real.csv"
    source.write_bytes(b"safe\n")
    target = tmp_path / "performance.csv"
    target.unlink()
    try:
        target.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(module.InputValidationError, match="regular file"):
        module.validate_inputs(manifest)


def test_reset_executes_without_input_manifest_validation(monkeypatch) -> None:
    module = _load_module()
    calls: list[tuple[str, list[str]]] = []

    def fail_validation(_path: Path):
        raise AssertionError("reset must not depend on mounted input data")

    def record_exec(executable: str, argv: list[str]) -> None:
        calls.append((executable, argv))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(module, "validate_inputs", fail_validation)
    monkeypatch.setattr(os, "execv", record_exec)
    with pytest.raises(RuntimeError, match="intercepted"):
        module.main(["reset"])
    assert calls
    assert calls[0][1][-2:] == ["reset", "--yes"]


@pytest.mark.parametrize(
    ("argv", "expected_tail"),
    (
        (["demo-seed"], ["demo-seed"]),
        (
            ["demo-verify", "--expected-status", "RESOLVED"],
            ["demo-verify", "--expected-status", "RESOLVED"],
        ),
        (
            ["demo-verify", "--expected-status", "REOPENED"],
            ["demo-verify", "--expected-status", "REOPENED"],
        ),
    ),
)
def test_offline_demo_commands_validate_inputs_and_exec_only_fixed_arguments(
    monkeypatch, argv: list[str], expected_tail: list[str]
) -> None:
    module = _load_module()
    validated: list[bool] = []
    calls: list[tuple[str, list[str]]] = []

    def record_validation(_path=module.INPUT_MANIFEST):
        validated.append(True)
        return {"files": 6, "bytes": 1}

    def record_exec(executable: str, arguments: list[str]) -> None:
        calls.append((executable, arguments))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(module, "validate_inputs", record_validation)
    monkeypatch.setattr(os, "execv", record_exec)
    with pytest.raises(RuntimeError, match="intercepted"):
        module.main(argv)
    assert validated == [True]
    assert calls[0][1][-len(expected_tail) :] == expected_tail


def test_unknown_command_fails_without_exec() -> None:
    module = _load_module()
    with pytest.raises(SystemExit) as error:
        module.main(["shell"])
    assert error.value.code == 2


def test_governance_prepare_uses_only_the_fixed_loopback_contract(
    monkeypatch, capsys
) -> None:
    module = _load_module()
    incident_id = "incident-safe-1"
    action_hash = "a" * 64
    opener = _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            incident_id,
            status="AWAITING_APPROVAL",
            revision=4,
            action_hash=action_hash,
        ),
    )

    assert module.main(["governance-prepare", incident_id]) == 0

    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert request.full_url == (
        "http://127.0.0.1:8085/local/v1/incidents/incident-safe-1/prepare"
    )
    assert request.get_method() == "POST"
    assert timeout == module.GOVERNANCE_HTTP_TIMEOUT_SECONDS == 7.0
    assert timeout > 5.0
    assert timeout <= 10.0
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Connection") == "close"
    assert request.get_header("X-networkagent-local-operation") == "governance-v1"
    request_body = json.loads(request.data)
    assert request_body["actor"] == "local-container-governance"
    assert set(request_body) == {"actor", "idempotency_key"}
    assert request_body["idempotency_key"].startswith(
        "local-container-governance-prepare-v1-"
    )
    assert len(request_body["idempotency_key"].rsplit("-", 1)[1]) == 64
    assert opener.handlers[0].proxies == {}

    assert json.loads(capsys.readouterr().out) == {
        "action_hash": action_hash,
        "command": "governance-prepare",
        "incident_id": incident_id,
        "ok": True,
        "replayed": False,
        "revision": 4,
        "status": "AWAITING_APPROVAL",
    }


def test_governance_prepare_idempotency_is_stable(monkeypatch, capsys) -> None:
    module = _load_module()
    incident_id = "incident-stable"
    opener = _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            incident_id,
            status="AWAITING_APPROVAL",
            revision=4,
        ),
    )

    assert module.main(["governance-prepare", incident_id]) == 0
    assert module.main(["governance-prepare", incident_id]) == 0
    bodies = [json.loads(request.data) for request, _timeout in opener.calls]
    assert bodies[0]["idempotency_key"] == bodies[1]["idempotency_key"]
    capsys.readouterr()


@pytest.mark.parametrize("terminal_status", ("RESOLVED", "REOPENED"))
def test_governance_prepare_accepts_only_approved_terminal_exact_replay(
    monkeypatch, capsys, terminal_status: str
) -> None:
    module = _load_module()
    incident_id = "incident-terminal-replay"
    action_hash = "d" * 64
    _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            incident_id,
            status=terminal_status,
            revision=7,
            action_hash=action_hash,
            approval_status="APPROVED",
            replayed=True,
        ),
    )

    assert module.main(["governance-prepare", incident_id]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "action_hash": action_hash,
        "command": "governance-prepare",
        "incident_id": incident_id,
        "ok": True,
        "replayed": True,
        "revision": 7,
        "status": terminal_status,
    }


def test_governance_prepare_rejects_nonterminal_replayed_response(monkeypatch) -> None:
    module = _load_module()
    _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            "incident-replayed",
            status="AWAITING_APPROVAL",
            revision=4,
            replayed=True,
        ),
    )
    with pytest.raises(module.GovernanceCommandError, match="response contract"):
        module.main(["governance-prepare", "incident-replayed"])


def test_governance_decide_requires_and_binds_prepare_hash_and_revision(
    monkeypatch, capsys
) -> None:
    module = _load_module()
    incident_id = "incident-safe-2"
    action_hash = "b" * 64
    opener = _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            incident_id,
            status="REMEDIATING",
            revision=5,
            action_hash=action_hash,
            approval_status="APPROVED",
        ),
    )

    assert module.main(["governance-decide", incident_id, action_hash, "4"]) == 0

    request, _timeout = opener.calls[0]
    assert request.full_url.endswith("/incident-safe-2/decide")
    body = json.loads(request.data)
    assert body == {
        "actor": "local-container-governance",
        "approve": True,
        "expected_action_hash": action_hash,
        "expected_revision": 4,
        "idempotency_key": body["idempotency_key"],
        "reason": "approve exact side-effect-free local simulation",
    }
    assert body["idempotency_key"].startswith("local-container-governance-decide-v1-")
    assert json.loads(capsys.readouterr().out) == {
        "command": "governance-decide",
        "incident_id": incident_id,
        "ok": True,
        "replayed": False,
        "status": "REMEDIATING",
    }


@pytest.mark.parametrize("terminal_status", ("RESOLVED", "REOPENED"))
def test_terminal_full_chain_replay_accepts_prepare_and_decide_responses(
    monkeypatch, capsys, terminal_status: str
) -> None:
    module = _load_module()
    incident_id = "incident-full-chain-replay"
    action_hash = "e" * 64
    responses = [
        _HttpResponse(
            _governance_payload(
                incident_id,
                status=terminal_status,
                revision=7,
                action_hash=action_hash,
                approval_status="APPROVED",
                replayed=True,
            )
        ),
        _HttpResponse(
            _governance_payload(
                incident_id,
                status=terminal_status,
                revision=7,
                action_hash=action_hash,
                approval_status="APPROVED",
                replayed=True,
            )
        ),
    ]
    opener = _SequenceHttpOpener(responses)
    monkeypatch.setattr(module, "build_opener", lambda *_handlers: opener)
    monkeypatch.setattr(module, "validate_inputs", lambda: {"files": 6, "bytes": 1})

    assert module.main(["governance-prepare", incident_id]) == 0
    assert module.main(["governance-decide", incident_id, action_hash, "4"]) == 0

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["command"] for item in output] == [
        "governance-prepare",
        "governance-decide",
    ]
    assert all(item["status"] == terminal_status for item in output)
    assert all(item["replayed"] is True for item in output)
    assert len(opener.calls) == 2


@pytest.mark.parametrize(
    ("outcome", "incident_status", "verification_status", "expected_boolean"),
    (
        ("passed", "RESOLVED", "PASSED", True),
        ("failed", "REOPENED", "FAILED", False),
    ),
)
def test_governance_execute_has_only_fixed_verification_outcomes(
    monkeypatch,
    capsys,
    outcome: str,
    incident_status: str,
    verification_status: str,
    expected_boolean: bool,
) -> None:
    module = _load_module()
    incident_id = "incident-safe-3"
    opener = _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            incident_id,
            status=incident_status,
            revision=6,
            approval_status="APPROVED",
            verification_status=verification_status,
        ),
    )

    assert module.main(["governance-execute", incident_id, outcome]) == 0

    request, _timeout = opener.calls[0]
    assert request.full_url.endswith("/incident-safe-3/execute")
    body = json.loads(request.data)
    assert body["actor"] == "local-container-governance"
    assert body["verification_passed"] is expected_boolean
    assert set(body) == {"actor", "idempotency_key", "verification_passed"}
    assert body["idempotency_key"].startswith("local-container-governance-execute-v1-")
    assert json.loads(capsys.readouterr().out) == {
        "command": "governance-execute",
        "incident_id": incident_id,
        "ok": True,
        "replayed": False,
        "status": incident_status,
    }


@pytest.mark.parametrize(
    "arguments",
    (
        ["governance-prepare", "../incident"],
        ["governance-prepare", "http://example.invalid"],
        ["governance-prepare", "incident/child"],
        ["governance-decide", "incident-safe", "A" * 64, "4"],
        ["governance-decide", "incident-safe", "a" * 63, "4"],
        ["governance-decide", "incident-safe", "a" * 64, "-1"],
        ["governance-decide", "incident-safe", "a" * 64, "+1"],
        ["governance-decide", "incident-safe", "a" * 64, "01"],
        ["governance-execute", "incident-safe", "true"],
    ),
)
def test_governance_cli_rejects_unbounded_or_ambiguous_arguments(arguments) -> None:
    module = _load_module()
    with pytest.raises(SystemExit) as error:
        module.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "payload",
    (
        _governance_payload(
            "wrong-incident",
            status="AWAITING_APPROVAL",
            revision=4,
        ),
        _governance_payload(
            "incident-safe",
            status="REMEDIATING",
            revision=4,
        ),
        _governance_payload(
            "incident-safe",
            status="AWAITING_APPROVAL",
            revision=4,
            action_hash="c" * 64,
            approval_status="APPROVED",
        ),
    ),
)
def test_governance_prepare_rejects_contract_drift(monkeypatch, payload) -> None:
    module = _load_module()
    _install_http_response(monkeypatch, module, payload)
    with pytest.raises(module.GovernanceCommandError, match="response contract"):
        module.main(["governance-prepare", "incident-safe"])


def test_governance_decide_rejects_wrong_response_hash(monkeypatch) -> None:
    module = _load_module()
    _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            "incident-safe",
            status="REMEDIATING",
            revision=5,
            action_hash="c" * 64,
            approval_status="APPROVED",
        ),
    )
    with pytest.raises(module.GovernanceCommandError, match="response contract"):
        module.main(["governance-decide", "incident-safe", "a" * 64, "4"])


def test_governance_execute_rejects_wrong_verification_contract(monkeypatch) -> None:
    module = _load_module()
    _install_http_response(
        monkeypatch,
        module,
        _governance_payload(
            "incident-safe",
            status="RESOLVED",
            revision=6,
            approval_status="APPROVED",
            verification_status="FAILED",
        ),
    )
    with pytest.raises(module.GovernanceCommandError, match="response contract"):
        module.main(["governance-execute", "incident-safe", "passed"])


def test_governance_response_budget_is_enforced(monkeypatch) -> None:
    module = _load_module()
    response = _HttpResponse({"ok": True, "data": {}})
    response.body = b"x" * (module.MAX_HTTP_RESPONSE_BYTES + 1)
    response.headers.replace_header("Content-Length", str(len(response.body)))
    opener = _HttpOpener(response)
    monkeypatch.setattr(module, "build_opener", lambda *_handlers: opener)
    monkeypatch.setattr(module, "validate_inputs", lambda: {"files": 6, "bytes": 1})

    with pytest.raises(module.GovernanceCommandError, match="byte limit"):
        module.main(["governance-prepare", "incident-safe"])


def test_governance_rejects_overlong_numeric_content_length_safely(
    monkeypatch,
) -> None:
    module = _load_module()
    response = _HttpResponse({"ok": True, "data": {}})
    response.headers.replace_header("Content-Length", "9" * 5_000)
    opener = _HttpOpener(response)
    monkeypatch.setattr(module, "build_opener", lambda *_handlers: opener)
    monkeypatch.setattr(module, "validate_inputs", lambda: {"files": 6, "bytes": 1})

    with pytest.raises(module.GovernanceCommandError, match="byte limit"):
        module.main(["governance-prepare", "incident-safe"])
