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

## Local governance HTTP API

The same loopback-only Starlette application exposes a separate, versioned
Local Governance surface while leaving the A2A root and Agent Card unchanged:

- `GET /local/v1/incidents/{incident_id}` returns the safe governance view.
- `POST /local/v1/incidents/{incident_id}/prepare` runs deterministic local
  triage and RCA and prepares an approval-bound proposal.
- `POST /local/v1/incidents/{incident_id}/decide` records an explicit decision
  bound to the returned action hash and Incident revision.
- `POST /local/v1/incidents/{incident_id}/execute` performs and verifies only
  the fixed, side-effect-free `LOCAL_SIMULATION` action.

Every governance request requires a loopback `Host`. Every POST additionally
requires `Content-Type: application/json` and
`X-NetworkAgent-Local-Operation: governance-v1`. Request bodies are limited to
64 KiB and responses to 256 KiB. Inputs use strict, extra-forbidden schemas;
responses expose only a safe Incident summary, RCA conclusion, action hash and
scope, approval status, and action/verification status.

The operation header is an explicit local-operation intent guard, not an
authentication mechanism. Governance requests are supported only through the
official runner bound directly to a loopback interface; both the HTTP `Host`
and the connected client address must be loopback. Do not expose this API via
an external bind, port forward, or reverse proxy.

## Local operational probes

The same direct loopback application exposes three read-only probes. They do
not require an operation header, accept no query string, perform no write, and
still require both the HTTP `Host` and connected peer to be loopback:

- `GET /local/v1/healthz` proves only that the process can answer locally. It
  does not read DuckDB and does not imply dependency or Cloud readiness.
- `GET /local/v1/readyz` performs one bounded, one-second Canonical Incident
  repository read. It returns `200` only for the supported Local Profile and a
  fixed `503 LOCAL_SERVICE_NOT_READY` response on dependency failure.
- `GET /local/v1/version` returns only the service/package, local HTTP,
  Replay-schema, and Domain-schema versions. This allowlisted JSON is useful
  for diagnostics but is not a signed build attestation.

The probe response budget is 4 KiB. They expose no filesystem path,
environment variable, raw dependency exception, credential, Incident content,
or build identity. Their supported deployment remains the foreground runner
bound directly to loopback; a reverse proxy can hide the real peer and is not
supported.

This API is not a Fault ingestion endpoint and does not connect to Pub/Sub,
Cloud MCP, Engineer, GitOps, GKE, or a Network Operator. It cannot perform a
real network change.

## Local canonical Fault receiver

`POST /local/v1/faults/replay` is the separate loopback-only ingress for the
public `telco-lab` replay wire contract. It requires both a loopback `Host` and
a directly connected loopback client, `Content-Type: application/json`,
`X-NetworkAgent-Local-Operation: replay-v1`, and an `Idempotency-Key` exactly
matching the validated wire payload. The operation header expresses local
intent; it is not authentication. Use this endpoint only through the official
loopback runner, never through an external bind, proxy, or port forward.

Requests are capped at 256 KiB and responses at 64 KiB. The receiver accepts
only the frozen BubbleRAN dataset/version and persistent-interference scenario,
with a `5G_SA` `lab:5g-sa:gnb:*` resource. It delegates wire identity and
privacy validation to the public `telco-lab` contract. A `202` receipt is sent
only after the Incident, audit event, idempotency record, and immutable source
association have committed, then the current Incident, initial audit binding,
and source association have been read back. Exact replay fails closed if any
part of that durable snapshot is missing or inconsistent. The
receipt exposes only the source and payload hashes, Incident identifier,
original status/revision, technology, and safe resource scope. Exact retries,
including retries after later governance, return the original receipt without
another write.

Each source event intentionally owns a separate deterministic Incident; this
slice does not perform cross-event or time-window aggregation. Only the exact
controlled local rule (`ran.mac.ul_bler` greater than `0.15`) adds rule-backed
evidence that can produce a conclusive RCA. Other valid events remain durably
audited but non-actionable. The threshold is a local test fixture and must not
be interpreted as a production-network rule.

The receiver does not ingest Pub/Sub events, use Spanner or GCP credentials,
call Cloud MCP or Engineer, expose a legacy/public Fault route, approve an
action, or make a real network change. Any later execution remains the separate
approval-gated, side-effect-free `LOCAL_SIMULATION` governance flow above.

## Local HTTP capacity and timeout boundary

The supported foreground runner uses the repository's bounded h11 protocol and
does not trust proxy headers. It admits at most 32 live TCP transports. Every
initial or keep-alive request header has a one-second absolute budget. Local
Governance, Fault, and A2A requests share one pre-body admission slot with no
queue and a two-second body deadline; Governance and Fault operations then
share one isolated business worker with no queue and a five-second operation
deadline. Busy, header/body timeout, unknown local path, and unsupported
standard methods return fixed JSON where a response can still be delivered;
the connection is closed and rejected sockets are never retained to improve
error delivery.

If a Governance/Fault operation times out or its caller disconnects, the
worker is not cancelled because the DuckDB transaction outcome may already be
durable. The service reports an uncertain/busy result until that operation
settles, after which the same idempotency key recovers the exact repository
result. Do not retry with changed payload, actor, hash, revision, or outcome.

Header and body timers run cooperatively on the ASGI event loop. Governance and
Fault repository work is isolated from that loop, but legacy A2A SDK/store
operations may still perform synchronous work there. This release therefore
does not claim a hard wall-clock deadline for a globally blocked A2A loop.
A2A non-blocking/streaming background task lifetime is also outside the
synchronous request admission lease.
