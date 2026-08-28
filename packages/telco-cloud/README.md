# telco-cloud

Cloud Profile adapters for Canonical Incident, safe telemetry, and durable
source-event ingestion on Google Cloud Spanner. Infrastructure clients are
always injected; importing or constructing an adapter never reads credentials,
opens a network connection, or mutates a database schema.

## Explicit administration

- `telco-cloud-schema apply` is the only schema mutation entry point. The
  emulator branch applies object DDL only because the emulator does not support
  IAM/FGAC; production also reconciles the exact role matrix.
- `telco-cloud-migrate` exports a checksummed, bounded Canonical snapshot from
  an initialized DuckDB profile and imports eligible DETECTED/revision-0 rows
  through the short-lived `telco_migration_importer` role.
- Runtime repositories never perform DDL. Legacy Spanner Incident rows that
  need semantic mapping are quarantined instead of being guessed or dual-written.

See the [P3 migration runbook](../../docs/runbooks/p3-canonical-migration.md)
and [Cloud deployment guide](../../deploy/cloud/p3/README.md).
