"""Cloud Profile Spanner adapters with injected infrastructure handles."""

from .config import (
    CloudProfileConfig,
    CloudSchemaAdminConfig,
    compose_spanner_admin_database,
    compose_spanner_database,
)
from .event_ingest import (
    IngestDisposition,
    IngestResult,
    SourceEventEnvelope,
    SpannerEventIngestRepository,
)
from .incident_repository import (
    MAX_REPOSITORY_BATCH_BYTES,
    SpannerIncidentRepository,
)
from .migration import (
    MAX_MIGRATION_BUNDLE_BYTES,
    MAX_MIGRATION_INCIDENTS,
    MIGRATION_SCHEMA_VERSION,
    MigrationBundle,
    MigrationBundleError,
    MigrationDependencyError,
    MigrationEntry,
    MigrationReport,
    QuarantineCode,
    QuarantineItem,
    create_migration_bundle,
    dump_migration_bundle,
    export_migration_bundle,
    import_migration_bundle,
    load_migration_bundle,
    validate_migration_bundle,
)
from .outbox_repository import (
    OutboxLeaseConflictError,
    OutboxRecord,
    OutboxStatus,
    SpannerOutboxRepository,
)
from .profile import CloudKpiDetectionService
from .schema import (
    CANONICAL_SCHEMA_DDL,
    FAULT_DATABASE_ROLE,
    MCP_DATABASE_ROLE,
    MIGRATION_DATABASE_ROLE,
    OUTBOX_DATABASE_ROLE,
    apply_object_schema,
    apply_schema,
)
from .telemetry_repository import SpannerTelemetryRepository


__all__ = [
    "CANONICAL_SCHEMA_DDL",
    "CloudProfileConfig",
    "CloudSchemaAdminConfig",
    "CloudKpiDetectionService",
    "FAULT_DATABASE_ROLE",
    "IngestDisposition",
    "IngestResult",
    "MCP_DATABASE_ROLE",
    "MIGRATION_DATABASE_ROLE",
    "MAX_REPOSITORY_BATCH_BYTES",
    "MAX_MIGRATION_BUNDLE_BYTES",
    "MAX_MIGRATION_INCIDENTS",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationBundle",
    "MigrationBundleError",
    "MigrationDependencyError",
    "MigrationEntry",
    "MigrationReport",
    "OUTBOX_DATABASE_ROLE",
    "OutboxLeaseConflictError",
    "OutboxRecord",
    "OutboxStatus",
    "QuarantineCode",
    "QuarantineItem",
    "SourceEventEnvelope",
    "SpannerEventIngestRepository",
    "SpannerIncidentRepository",
    "SpannerOutboxRepository",
    "SpannerTelemetryRepository",
    "apply_object_schema",
    "apply_schema",
    "create_migration_bundle",
    "compose_spanner_admin_database",
    "compose_spanner_database",
    "dump_migration_bundle",
    "export_migration_bundle",
    "import_migration_bundle",
    "load_migration_bundle",
    "validate_migration_bundle",
]
