"""Run the shared contract against the in-memory and DuckDB adapters."""

from __future__ import annotations

import asyncio

import pytest

from telco_domain import InMemoryIncidentRepository
from telco_local import DuckDbIncidentRepository

from .repository_contract import (
    ContractClock,
    assert_incident_repository_contract,
)


@pytest.mark.parametrize("adapter", ("memory", "duckdb"))
def test_repository_adapter_contract(adapter: str, tmp_path) -> None:
    clock = ContractClock()
    if adapter == "memory":
        repository = InMemoryIncidentRepository(clock=clock)
    else:
        repository = DuckDbIncidentRepository(
            tmp_path / "repository-contract.duckdb",
            clock=clock,
        )
    asyncio.run(assert_incident_repository_contract(repository, clock))
