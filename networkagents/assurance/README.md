# Local Assurance Agent

This package exposes the deterministic Local Profile through A2A SDK 0.3.11.
It has no ADK, model, GCP, or credential dependency. Free-form text is display
only; every operation is selected by one strict `DataPart`.

The runtime supports three workflows:

- `assurance_scan_request` previews bounded LTE incident candidates. A page
  containing candidates ends in `input-required` with a short-lived,
  server-owned challenge. An empty page is emitted as an artifact and the task
  completes without a write.
- `assurance_confirmation_request` must continue the original task and context.
  `CONFIRM` rescans the exact snapshot and performs the only incident write;
  `REJECT` completes with zero incident writes.
- `assurance_analyze_request` loads the Incident from server storage and returns
  the deterministic, read-only `rca_result` contract.

Bootstrap and runtime are deliberately separate. From the repository root:

```powershell
telco-assurance-agent init `
  --database .data/local.duckdb `
  --performance-csv data/samples/lte-demo/performance.csv `
  --safe-trace-csv data/samples/lte-demo/safe-cell-traces.csv `
  --rules-dir data/rca-rules/lte `
  --public-url http://127.0.0.1:8085/

telco-assurance-agent run `
  --database .data/local.duckdb `
  --performance-csv data/samples/lte-demo/performance.csv `
  --safe-trace-csv data/samples/lte-demo/safe-cell-traces.csv `
  --rules-dir data/rca-rules/lte `
  --public-url http://127.0.0.1:8085/
```

`run` opens an initialized database and never performs schema creation or data
imports. This P2b service is intentionally local-only: both the bind host and
the trusted AgentCard `public_url` must be loopback addresses. The standard
endpoints are `/.well-known/agent-card.json` and `/` for JSON-RPC; cancellation
uses `tasks/cancel`.
