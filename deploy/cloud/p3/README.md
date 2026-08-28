# P3 Cloud Profile deployment boundary

This directory documents the production boundary for the Canonical Spanner,
fault-ingress, and read-only MCP services introduced in P3. It is deliberately
separate from the legacy `install.sh` path: enabling the new writer is a
controlled cutover, never a dual-write deployment.

## Components and identities

| Component | Runtime identity | Spanner database role | Public access |
|---|---|---|---|
| `telco-fault-ingress` | dedicated fault-writer service account | `telco_fault_writer` | disabled; Pub/Sub push identity receives `roles/run.invoker` |
| `telco-cloud-mcp` | dedicated assurance-reader service account | `telco_mcp_reader` | disabled; only approved agent identities receive `roles/run.invoker` |
| outbox dispatcher | dedicated delivery-worker service account | `telco_outbox_dispatcher` | no public HTTP endpoint |
| migration import job | short-lived importer service account | `telco_migration_importer` | no HTTP endpoint; remove its IAM binding after import verification |
| schema job | short-lived release service account | schema administration only during the job | no HTTP endpoint |

Do not grant the MCP identity write access. The dispatcher may select Outbox
rows and update only `status`, `attempts`, `available_at`, `published_at`,
`lease_owner`, `lease_expires_at`, and `last_error_code`; it cannot update the
payload or identity columns and receives no `INSERT` or `DELETE`. Do not reuse
the legacy Network Tools service account because that process exposes
engineering mutations. Do not grant any runtime identity
`roles/spanner.databaseReader` or
`roles/spanner.databaseUser`: those database-wide roles would also expose
legacy/raw tables when the schemas share a database.

Exact migration replay needs to read the persisted Incident, source,
Audit, idempotency, and active-key rows before it can safely acknowledge an
import replay. Give that access only to the short-lived
`telco_migration_importer` role. It has `SELECT` and `INSERT` on those five
tables and no `UPDATE`, `DELETE`, Inbox, Outbox, telemetry, evidence, resource,
legacy, or raw access. Never add Audit `SELECT` to the continuously running
`telco_fault_writer` merely to support a migration job.

The privilege matrix in
`packages/telco-cloud/src/telco_cloud/schema.py` is the single source of truth.
[`spanner-fgac.sql`](./spanner-fgac.sql) is its reviewed one-time production
bootstrap mirror; never change one without the deployment consistency test.
Use either the production `telco-cloud-schema apply` command, which reconciles
the same matrix, or apply this SQL once with the short-lived schema identity;
do not run both bootstrap paths against an already-created role set. Grant each
runtime service account `roles/spanner.fineGrainedAccessUser` and a
conditioned `roles/spanner.databaseRoleUser` binding for exactly its named
database role. The IAM condition must include both the DatabaseRole resource
type and the full database-role resource name. Each process must then set
`TELCO_SPANNER_DATABASE_ROLE`; runtime composition passes that role to the
Spanner client. A production process that omits the role must fail to start.

Validate the checked-in mirror with:

```text
python -m pytest deploy/cloud/p3/test_spanner_fgac_artifact.py -q
```

## Required resources

Create a dedicated fault topic, a push subscription, and a dead-letter topic.
The subscription must use authenticated OIDC push to
`/events/pubsub`, set `max-delivery-attempts` to 5, and use bounded retry delays.
Grant the Pub/Sub service agent the publisher role on the dead-letter topic and
the subscriber role on the source subscription, as required for dead-letter
forwarding.

The push envelope's `subscription` value must exactly match the value configured
in `FAULT_ALLOWED_SUBSCRIPTIONS`, for example:

```text
projects/PROJECT_ID/subscriptions/telco-fault-canonical-v2
```

Both Cloud Run services must be deployed with unauthenticated access disabled.
The fault push service account alone receives invoker permission on fault
ingress. MCP callers receive invoker permission on the MCP service separately.

## Safe cutover

The first committed P3 release is the supported baseline for the
`Canonical*V2` schema. Any database created from an uncommitted P3 development
snapshot must be discarded and recreated before release; intermediate working
tree DDL is not a migration source. After this baseline, all schema changes
must use explicit expand-first migrations and preserve rollback compatibility.

1. Apply the Canonical v2 schema with the explicit schema job. Do not modify or
   rename the legacy `Incident` table in place.
2. Deploy fault ingress with `FAULT_PIPELINE_MODE=shadow`. Shadow mode records a
   durable inbox receipt only; it does not write a Canonical Incident or Outbox.
3. Verify invalid messages reach the Pub/Sub dead-letter topic after the
   configured delivery attempts and transient Spanner failures are retried.
4. Pause the legacy writer/Eventarc trigger, verify that no legacy write is in
   flight, then change the new service to `FAULT_PIPELINE_MODE=canonical`.
5. To roll back, set the new service to `paused` first, restore the legacy
   writer, and verify ownership before accepting new events. Never run both
   writers as primary.

`legacy` mode exists only for an explicitly injected compatibility handler; the
new service's production entry point intentionally refuses to invent or import
that handler.

## Required configuration

Use Secret Manager or the runtime identity for credentials; no credential value
belongs in environment files. The non-secret configuration is:

```text
GOOGLE_PROJECT=PROJECT_ID
GOOGLE_SPANNER_INSTANCE=INSTANCE_ID
GOOGLE_SPANNER_DATABASE=DATABASE_ID
TELCO_SPANNER_DATABASE_ROLE=telco_fault_writer  # fault service
# TELCO_SPANNER_DATABASE_ROLE=telco_mcp_reader  # MCP service
# TELCO_SPANNER_DATABASE_ROLE=telco_outbox_dispatcher  # dispatcher
# TELCO_SPANNER_DATABASE_ROLE=telco_migration_importer  # short-lived importer
FAULT_ALLOWED_SUBSCRIPTIONS=projects/PROJECT_ID/subscriptions/SUBSCRIPTION_ID
FAULT_PIPELINE_MODE=shadow
FAULT_INGRESS_HOST=0.0.0.0
TELCO_CLOUD_MCP_HOST=0.0.0.0
PORT=8080
```

Do not set `SPANNER_EMULATOR_HOST` in a Cloud Run deployment. The composition
roots reject an emulator configuration mismatch.

## Verification and rollback evidence

Before canonical cutover, record:

- IAM policy showing that neither service allows `allUsers`;
- conditioned DatabaseRoleUser bindings proving that each service can assume
  only its own fine-grained role, plus negative access tests against a legacy
  or raw table;
- the push subscription's OIDC identity, retry policy, and dead-letter topic;
- one exact replay producing a single Inbox row and a single active Incident;
- one poison event arriving in the dead-letter topic without payload reflection
  in service logs;
- MCP tool enumeration containing exactly the six read-only tools;
- an outbox-dispatcher negative test proving that payload and identity columns
  cannot be updated;
- a migration-importer negative test proving that Inbox, Outbox, telemetry,
  evidence, resource, legacy, and raw tables cannot be read or written;
- the schema version, release image digest, and rollback owner.

Spanner Emulator validates DDL, transactions, retry/idempotency, and concurrency
in CI. It does not validate IAM, TLS, Workload Identity, production quotas, or
latency; those checks remain a Cloud staging gate.
