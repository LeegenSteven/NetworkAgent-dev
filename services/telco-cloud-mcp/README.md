# Telco Cloud MCP

This is a separate, read-only FastMCP service for canonical Incidents and
bounded telemetry evidence. It does not import the legacy network tools server,
does not register engineering or Incident mutation tools, and does not connect
to Spanner while the module is imported.

The six registered tools are:

1. `get_canonical_incident`
2. `list_canonical_incidents`
3. `get_incident_history`
4. `collect_incident_evidence`
5. `query_kpi_observations`
6. `resolve_resource_references`

All responses are structured, privacy checked, limited to 256 KiB, and use
fixed safe error codes. Time windows are UTC and at most 31 days, resource
scopes contain at most 100 identifiers, and result limits are hard bounded.
Deploy this service with a read-only service account and authenticated internal
invocation; MCP `readOnlyHint` is advisory and is not an authorization control.
Outside the emulator, `TELCO_SPANNER_DATABASE_ROLE` is mandatory and must be
exactly `telco_mcp_reader`, a dedicated FGAC role limited to the canonical Incident, audit,
resource-reference, safe-evidence, and KPI projections. It must be different
from the Fault Ingress writer role; do not rely on the six-tool allowlist as a
database authorization boundary.
