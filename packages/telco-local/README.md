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

## Local deployment and governance loop

`tools/local-stack/local_stack.py` is the repository-level entry point for the
credential-free Local Profile. It uses the bundled LTE sample, rules, and
documents; stores state below an explicitly selected marker-owned workspace;
emits one JSON document per command; and defaults to `ACTION_MODE=disabled`.
Run it from the repository root with Python 3.12 or 3.13:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack doctor
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack init
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack status
```

`doctor` is read-only. `init` creates the DuckDB and, when its optional
dependencies are available, the Assurance schema. Repeating `init` on the same
owned workspace is safe; a failed first initialization rolls back only entries
created by that attempt.

The governance demo is intentionally two-stage. First confirm the deterministic
candidate and persist the RCA proposal. The command stops at
`AWAITING_APPROVAL` and returns a safe `action_preview` containing the
`action_hash`, resource scope, risk, and `expected_revision`:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py `
  --workspace .local/networkagent-stack `
  --action-mode simulate `
  demo --confirm-incident
```

After reviewing that output, copy both binding values into a separate approval
command. A non-empty reason is mandatory:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py `
  --workspace .local/networkagent-stack `
  --action-mode simulate `
  demo `
  --approve-action `
  --reason "reviewed isolated local simulation" `
  --expected-action-hash HASH_FROM_PREVIEW `
  --expected-revision REVISION_FROM_PREVIEW
```

The lifecycle is
`DETECTED → TRIAGED → INVESTIGATING → RCA_COMPLETE → AWAITING_APPROVAL → REMEDIATING → VERIFYING`.
The only permitted action is the fixed, low-risk `LOCAL_SIMULATION`; its action
gateway creates a local `ActionRun` and performs no external I/O. A passed
deterministic verification reaches `RESOLVED`. Add
`--verification-outcome failed` to the approval command to exercise the
`REOPENED` path. Rejection, expiry, a stale revision, a changed action hash, or
an idempotency-key payload conflict produces no action run. The actor is part of
the immutable idempotency binding, and a retry resumes safely from durable
`REMEDIATING` or `VERIFYING` state after a response-loss interruption. If the
approved grant expires while execution is pending, a bound, replayable
zero-action transition closes that attempt as `FAILED`.

If the optional Assurance runtime is installed, `serve` runs it in the
foreground on the fixed loopback address. It accepts only disabled action mode:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack --port 8085 serve
```

The foreground service provides `GET /local/v1/healthz`,
`GET /local/v1/readyz`, and `GET /local/v1/version` for direct loopback
operations. `healthz` is process liveness only; `readyz` adds one bounded local
Incident-repository read and returns a fixed 503 when unavailable; `version`
is allowlisted diagnostic metadata rather than a signed build attestation.
These probes are unsupported behind a reverse proxy or port forward.

`reset` without `--yes` reports that confirmation is required. The confirmed
form removes only the marker-owned state and artifacts; it rejects roots, the
repository, home, symlink/junction/reparse workspaces, UNC/device paths,
non-fixed Windows drives, and unowned directories, and preserves unknown files:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack reset
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack reset --yes
```

This closes the governance lifecycle only for the isolated Local Profile. It
does not invoke the Cloud Resolver, Engineer Agent, MCP write tools, GitOps,
GKE, or the Network Operator, and it is not evidence for Cloud Staging IAM,
OIDC, Pub/Sub DLQ, or Workload Identity.
