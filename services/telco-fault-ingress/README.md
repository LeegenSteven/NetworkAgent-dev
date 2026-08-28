# Telco Fault Ingress

This service validates authenticated Pub/Sub push envelopes, converts approved
fault log types into canonical `SourceEventEnvelope` objects, and delegates one
durable ingest transaction to `telco-cloud`.

The default pipeline mode is `shadow`. The four mutually exclusive modes are
`legacy`, `shadow`, `canonical`, and `paused`. Only `canonical` owns canonical
Incident writes. Invalid payloads receive HTTP 400, transient dependency
failures receive HTTP 503, and only durable or idempotent outcomes receive HTTP
204.

A mode cutover acknowledges an exact durable replay in either direction. A
historical shadow result remains shadow-only, while a historical canonical
result keeps its original Incident/association/outbox shape; neither replay
promotes data nor double-writes. New non-replay results must still match the
currently selected mode exactly.

The service intentionally has no CORS middleware and never logs request bodies,
decoded log entries, credentials, subscriber identifiers, or exception text.
Deploy it behind Cloud Run IAM with an OIDC-authenticated Pub/Sub push
subscription and a dead-letter topic; do not enable unauthenticated invocation.
Outside the emulator, `TELCO_SPANNER_DATABASE_ROLE` is mandatory and must be
exactly `telco_fault_writer`, a dedicated FGAC role limited to the canonical Inbox, Incident,
association, audit, idempotency, active-key, and outbox tables. Do not grant
this process a whole-database Spanner writer role.
