from __future__ import annotations

import hashlib

import pytest


@pytest.fixture
def fixture_bytes() -> bytes:
    return b"timestamp,kpi,value\n2026-01-01T00:00:00Z,throughput,42\n"


@pytest.fixture
def catalog_mapping(fixture_bytes: bytes) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "catalog_id": "networkagent-open-data",
        "catalog_version": "1.0.0",
        "resources": [
            {
                "resource_id": "fixture.kpi.v1",
                "dataset_id": "fixture-kpi",
                "dataset_version": "1.0.0",
                "filename": "fixture-kpi.csv",
                "source_url": "https://datasets.example.test/releases/fixture.csv?token=secret",
                "allowed_hosts": ["datasets.example.test"],
                "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                "size_bytes": len(fixture_bytes),
                "media_type": "text/csv",
                "adapter": "fixture_csv_v1",
                "license": {
                    "id": "CC-BY-4.0",
                    "name": "Creative Commons Attribution 4.0",
                    "url": "https://creativecommons.org/licenses/by/4.0/",
                    "evidence_url": "https://datasets.example.test/LICENSE",
                    "evidence_sha256": "a" * 64,
                    "attribution": "Fixture dataset authors",
                    "reviewed_at": "2026-08-30",
                    "acceptance_required": True,
                },
            }
        ],
    }
