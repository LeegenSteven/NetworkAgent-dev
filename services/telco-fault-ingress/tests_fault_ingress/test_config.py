from __future__ import annotations

import pytest

from telco_fault_ingress.config import FaultIngressConfig, FaultPipelineMode

from .conftest import SUBSCRIPTION


def test_from_env_defaults_to_shadow_without_mutating_input() -> None:
    environ = {"FAULT_ALLOWED_SUBSCRIPTIONS": SUBSCRIPTION}
    config = FaultIngressConfig.from_env(environ)
    assert config.mode is FaultPipelineMode.SHADOW
    assert config.allowed_subscriptions == frozenset({SUBSCRIPTION})
    assert config.max_event_age_seconds == 7 * 24 * 60 * 60
    assert config.max_future_skew_seconds == 5 * 60
    assert environ == {"FAULT_ALLOWED_SUBSCRIPTIONS": SUBSCRIPTION}


@pytest.mark.parametrize("mode", tuple(FaultPipelineMode))
def test_all_modes_are_mutually_exclusive_values(mode: FaultPipelineMode) -> None:
    config = FaultIngressConfig(
        allowed_subscriptions=frozenset({SUBSCRIPTION}), mode=mode
    )
    assert config.mode is mode


def test_missing_subscription_allowlist_fails_closed() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FaultIngressConfig.from_env({})


def test_unknown_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="FAULT_PIPELINE_MODE"):
        FaultIngressConfig.from_env(
            {
                "FAULT_ALLOWED_SUBSCRIPTIONS": SUBSCRIPTION,
                "FAULT_PIPELINE_MODE": "canonical-and-legacy",
            }
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FAULT_MAX_EVENT_AGE_SECONDS", str(7 * 24 * 60 * 60 + 1)),
        ("FAULT_MAX_FUTURE_SKEW_SECONDS", str(5 * 60 + 1)),
    ],
)
def test_event_time_budgets_have_hard_upper_bounds(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        FaultIngressConfig.from_env(
            {"FAULT_ALLOWED_SUBSCRIPTIONS": SUBSCRIPTION, name: value}
        )
