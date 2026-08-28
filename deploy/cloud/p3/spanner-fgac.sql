-- One-time production bootstrap for P3 Spanner fine-grained access control.
-- Apply after the Canonical v2 schema with the short-lived schema identity.
-- These roles deliberately have no privileges on legacy/raw log or trace tables.
-- The authoritative matrix is telco_cloud/schema.py; CI checks this mirror.

CREATE ROLE telco_fault_writer;

GRANT SELECT, INSERT ON TABLE
  CanonicalIncidentsV2,
  CanonicalIncidentSourceEventsV2,
  CanonicalIncidentIdempotencyV2,
  CanonicalIncidentActiveKeysV2,
  CanonicalSourceEventInboxV2,
  CanonicalIncidentOutboxV2
TO ROLE telco_fault_writer;

GRANT INSERT ON TABLE
  CanonicalIncidentAuditV2
TO ROLE telco_fault_writer;

CREATE ROLE telco_mcp_reader;

GRANT SELECT ON TABLE
  CanonicalIncidentsV2,
  CanonicalIncidentAuditV2,
  SafeEvidenceReferencesV1,
  RadioKpiObservationsV1,
  CanonicalResourceReferencesV1
TO ROLE telco_mcp_reader;

CREATE ROLE telco_outbox_dispatcher;

GRANT SELECT ON TABLE
  CanonicalIncidentOutboxV2
TO ROLE telco_outbox_dispatcher;

GRANT UPDATE(
  status,
  attempts,
  available_at,
  published_at,
  lease_owner,
  lease_expires_at,
  last_error_code
) ON TABLE CanonicalIncidentOutboxV2
TO ROLE telco_outbox_dispatcher;

CREATE ROLE telco_migration_importer;

GRANT SELECT, INSERT ON TABLE
  CanonicalIncidentsV2,
  CanonicalIncidentSourceEventsV2,
  CanonicalIncidentAuditV2,
  CanonicalIncidentIdempotencyV2,
  CanonicalIncidentActiveKeysV2
TO ROLE telco_migration_importer;
