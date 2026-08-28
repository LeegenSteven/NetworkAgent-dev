# telco-local

`telco-local` contains the deterministic Local Profile adapters used by the
unified NetworkAgent incident workflow.  It imports LTE sample performance and
a projection of safe Cell Trace columns into DuckDB, calculates KPI views, and
implements the same `IncidentRepository` contract as `telco-domain`.

The package deliberately has no ADK, A2A, GCP, model-provider, or network
dependency.  Raw subscriber identifiers are never imported into the local
Cell Trace table.  All CSV timestamps require the explicitly configured `UTC`
source timezone.

## Development

From the repository root:

```powershell
python -m pip install -e packages/telco-domain
python -m pip install -e "packages/telco-local[test]"
python -m pytest packages/telco-local/tests -q
```

The default repository data assets are located under
`data/samples/lte-demo/`.  Callers construct `LocalProfileConfig` explicitly so
tests and local runs never silently select a database or source file.

P2a accepts LTE eNodeB and Cell identifier components only as one to nine ASCII
decimal digits whose numeric value is in `0..268435455`. This conservative
28-bit bound follows the E-UTRAN Cell Identity size in ETSI 3GPP TS 23.003
section 19.6. Accepted values are normalized to decimal without leading zeroes;
negative values, scientific notation, over-range values, and subscriber-sized
numeric strings fail closed without being echoed. Both CSV imports validate
before persistent table creation, and Telemetry revalidates database rows,
public resource selectors, and Incident evidence scopes.

`initialize_database(config, reset=True)` removes Local Profile tables and
their Incident history before rebuilding them.  Use `reset=True` only for an
explicitly selected disposable local database: it does not create an automatic
backup, so the removed history is not recoverable through `telco-local`.

P2a serializes writers inside one Python process, while DuckDB remains a
single-writer store across processes. Do not run multiple CLI/service writer
processes against the same database. Multi-process writer coordination or a
server database adapter is a later deployment-profile concern.

## Local assurance CLI

Installing this package registers `telco-local`; `python -m telco_local` is an
equivalent entry point. Every data path and correlation identifier is explicit:
the CLI does not read implicit cloud credentials, call an external model, or
enable network access.

Initialize the bundled LTE sample in a disposable database:

```powershell
telco-local --database-path .local/lte-demo.duckdb --performance-csv-path data/samples/lte-demo/performance.csv --safe-trace-csv-path data/samples/lte-demo/safe-cell-traces.csv --rules-dir data/rca-rules/lte --documents-dir data/docs/lte --source-timezone UTC init
```

Preview candidates without creating an Incident. The bundled assets currently
produce 15 deterministic episode candidates; use each result's `incident_id`
as the confirmation token. Envelope `message_id` and `idempotency_key` values
are fresh UUIDs and are not confirmation tokens.

The P2a CLI intentionally has no pagination: a scan fails closed above 100
candidates, above 1,000 samples in one episode, or above the bounded current
rule set. Windowed scan/pagination is a P2b follow-up; it must be added before
using this entry point with a dataset that exceeds these local-demo bounds.

```powershell
telco-local --database-path .local/lte-demo.duckdb --performance-csv-path data/samples/lte-demo/performance.csv --safe-trace-csv-path data/samples/lte-demo/safe-cell-traces.csv --rules-dir data/rca-rules/lte --documents-dir data/docs/lte --source-timezone UTC detect --trace-id trace-preview-001 --workflow-id workflow-preview-001
```

`confirm` is the only command that writes a Canonical Incident. It rescans the
server-owned data and rejects an `incident_id` when the previewed rule, resource,
window, source observations, or values have changed.

```powershell
telco-local --database-path .local/lte-demo.duckdb --performance-csv-path data/samples/lte-demo/performance.csv --safe-trace-csv-path data/samples/lte-demo/safe-cell-traces.csv --rules-dir data/rca-rules/lte --documents-dir data/docs/lte --source-timezone UTC confirm incident-from-detect-output --trace-id trace-confirm-001 --idempotency-key confirm-request-001 --actor operator --reason "用户明确确认创建 Incident"
```

Generate the eight-section Chinese RCA report without changing the stored
Incident. `rca` is an alias for `analyze`.

```powershell
telco-local --database-path .local/lte-demo.duckdb --performance-csv-path data/samples/lte-demo/performance.csv --safe-trace-csv-path data/samples/lte-demo/safe-cell-traces.csv --rules-dir data/rca-rules/lte --documents-dir data/docs/lte --source-timezone UTC analyze incident-from-confirm-output --trace-id trace-confirm-001 --workflow-id workflow-rca-001 --message-id message-rca-001 --idempotency-key rca-request-001 --report-version 1
```

All successful output is JSON using the P1 Canonical Incident/A2A contract.
Errors are JSON on standard error. There is deliberately no `--yes` or implicit
confirmation mode. `init --reset` is destructive only to the database selected
by `--database-path` and must be invoked explicitly.

Confirmation replay is checked before live telemetry is rescanned, so a genuine
retry survives later source changes. The Detector reconstructs the original
pre-commit candidate and delegates the replay to the repository, which verifies
the full actor/reason/trace request fingerprint; reusing the key with different
metadata is a deterministic conflict. When a previous request correlated to a
different active Incident, the original candidate must still be reproducible or
the replay fails closed.
