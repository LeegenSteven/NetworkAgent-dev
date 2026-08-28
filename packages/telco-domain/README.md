# telco-domain

`telco-domain` is the framework-independent core for the unified NetworkAgent
incident lifecycle. It contains versioned domain models, state transitions,
Agent-to-Agent payload contracts, repository/gateway protocols, privacy guards,
and deterministic in-memory test implementations.

The package intentionally does **not** depend on Google ADK, A2A SDK, FastMCP,
LangGraph, or a cloud provider SDK. Runtime services adapt these types at their
process boundaries so agents using different framework versions can interoperate.

## Safety invariants

- Incident state changes use optimistic revisions and an explicit transition map.
- Repository writes run the state machine and audit append in one atomic operation.
- Repeated idempotency keys are accepted only for an identical scoped request.
- Active incidents are deduplicated by correlation key or source-event identity.
- All timestamps are timezone-aware and normalized to UTC.
- Network actions require a trusted, unexpired approval bound to the Incident,
  immutable RCA report version, full action hash, and exact resource scope.
- Wire-level approval references are revalidated against the latest append-only
  decision and a gateway-owned clock immediately before execution.
- Contract payloads are privacy checked and limited to 256 KB and 24 levels.
- Raw IMSI, MSISDN, IMEISV, and SUPI values are rejected or redacted at model
  and audit-log boundaries.

## Development

From the repository root:

```powershell
python -m pip install -e "packages/telco-domain[dev]"
python -m pytest packages/telco-domain/tests -q
```

The tests are offline and must not require model credentials, GCP credentials,
network services, or a running Agent. The same suite is expected to pass on
Python 3.12 and 3.13 and across the Pydantic versions used by both source
projects.
