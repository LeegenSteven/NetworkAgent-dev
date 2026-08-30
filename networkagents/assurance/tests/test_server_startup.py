from __future__ import annotations

from types import SimpleNamespace

from telco_assurance_agent import __main__ as assurance_main
from telco_assurance_agent.transport_http import BoundedH11Protocol


def test_assurance_run_uses_fixed_hardened_uvicorn_budget(monkeypatch) -> None:
    config = SimpleNamespace(host="127.0.0.1", port=8085)
    application = object()
    captured = {}

    monkeypatch.setattr(assurance_main, "_config", lambda _arguments: config)
    monkeypatch.setattr(assurance_main, "create_app", lambda _config: application)
    monkeypatch.setattr(
        assurance_main.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(app=app, **kwargs),
    )

    assert (
        assurance_main.main(
            [
                "run",
                "--database",
                "unused.duckdb",
                "--performance-csv",
                "unused.csv",
                "--safe-trace-csv",
                "unused.csv",
                "--rules-dir",
                "unused-rules",
                "--public-url",
                "http://127.0.0.1:8085/",
            ]
        )
        == 0
    )
    assert captured == {
        "app": application,
        "host": "127.0.0.1",
        "port": 8085,
        "workers": 1,
        "reload": False,
        "interface": "asgi3",
        "lifespan": "on",
        "http": BoundedH11Protocol,
        "ws": "none",
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "access_log": False,
        "server_header": False,
        "date_header": False,
        "limit_concurrency": None,
        "backlog": 16,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 10,
        "h11_max_incomplete_event_size": 16_384,
    }
