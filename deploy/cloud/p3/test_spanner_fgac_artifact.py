from __future__ import annotations

from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "packages" / "telco-cloud" / "src" / "telco_cloud" / "schema.py"
DEPLOYMENT_SQL_PATH = Path(__file__).with_name("spanner-fgac.sql")


def _normalized(statement: str) -> str:
    return " ".join(statement.strip().rstrip(";").split())


def _statements_from_sql(text: str) -> tuple[str, ...]:
    without_comments = "\n".join(
        line.split("--", 1)[0] for line in text.splitlines()
    )
    return tuple(
        statement for statement in without_comments.split(";") if statement.strip()
    )


def _privilege_matrix(statements: tuple[str, ...]):
    roles: set[str] = set()
    table_grants: set[tuple[str, str, str]] = set()
    column_grants: set[tuple[str, str, str, str]] = set()

    for raw_statement in statements:
        statement = _normalized(raw_statement)
        role_match = re.fullmatch(r"CREATE ROLE ([A-Za-z_][A-Za-z0-9_]*)", statement)
        if role_match:
            roles.add(role_match.group(1))
            continue

        grant_match = re.fullmatch(
            r"GRANT (.+?) ON TABLE (.+?) TO ROLE ([A-Za-z_][A-Za-z0-9_]*)",
            statement,
        )
        if not grant_match:
            raise AssertionError(f"unsupported FGAC statement: {statement}")
        privilege_clause, table_clause, role = grant_match.groups()
        tables = tuple(item.strip() for item in table_clause.split(","))
        column_match = re.fullmatch(
            r"([A-Za-z]+)\(([^)]+)\)", privilege_clause
        )
        if column_match:
            if len(tables) != 1:
                raise AssertionError("column grants must target exactly one table")
            privilege, columns = column_match.groups()
            column_grants.update(
                (role, tables[0], column.strip(), privilege.upper())
                for column in columns.split(",")
            )
            continue
        table_grants.update(
            (role, table, privilege.strip().upper())
            for table in tables
            for privilege in privilege_clause.split(",")
        )
    return frozenset(roles), frozenset(table_grants), frozenset(column_grants)


def test_spanner_fgac_sql_matches_canonical_schema_privilege_matrix() -> None:
    schema = runpy.run_path(str(SCHEMA_PATH))
    canonical_statements = tuple(
        statement
        for statement in schema["CANONICAL_SCHEMA_DDL"]
        if statement.startswith(("CREATE ROLE ", "GRANT "))
    )
    deployment_statements = _statements_from_sql(
        DEPLOYMENT_SQL_PATH.read_text(encoding="utf-8")
    )

    deployment_matrix = _privilege_matrix(deployment_statements)
    assert deployment_matrix == _privilege_matrix(canonical_statements)

    roles, table_grants, column_grants = deployment_matrix
    assert "telco_migration_importer" in roles
    assert {
        ("telco_migration_importer", table, privilege)
        for table in (
            "CanonicalIncidentsV2",
            "CanonicalIncidentSourceEventsV2",
            "CanonicalIncidentAuditV2",
            "CanonicalIncidentIdempotencyV2",
            "CanonicalIncidentActiveKeysV2",
        )
        for privilege in ("SELECT", "INSERT")
    } == {
        item for item in table_grants if item[0] == "telco_migration_importer"
    }
    assert not any(item[0] == "telco_migration_importer" for item in column_grants)
    assert (
        "telco_fault_writer",
        "CanonicalIncidentAuditV2",
        "SELECT",
    ) not in table_grants
