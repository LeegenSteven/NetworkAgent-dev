from __future__ import annotations

import asyncio

import pytest

from telco_domain import DocumentRepository
import telco_local.documents as documents_module
from telco_local.documents import MarkdownDocumentRepository


def test_markdown_repository_ranks_chunks_and_returns_port_mappings(tmp_path) -> None:
    (tmp_path / "erab.md").write_text(
        "# ERAB\nS1 安全配置失败会影响 ERAB 建立成功率。\n\n"
        "## 建议\n检查失败结果的聚合占比。",
        encoding="utf-8",
    )
    (tmp_path / "rssi.md").write_text(
        "# RSSI\n上行 RSSI 用于观察无线信号。", encoding="utf-8"
    )
    repository = MarkdownDocumentRepository(tmp_path, technology="LTE")

    result = asyncio.run(
        repository.search("ERAB S1 安全配置失败", technology="LTE", limit=2)
    )

    assert isinstance(repository, DocumentRepository)
    assert result
    assert result[0]["title"] == "erab.md"
    assert result[0]["uri"].startswith("document://local/")
    assert "excerpt" in result[0]
    assert 0 < result[0]["score"] <= 1


def test_markdown_repository_is_stable_and_honors_technology_scope(tmp_path) -> None:
    (tmp_path / "b.md").write_text("# B\nsame phrase", encoding="utf-8")
    (tmp_path / "a.md").write_text("# A\nsame phrase", encoding="utf-8")
    repository = MarkdownDocumentRepository(tmp_path, technology="LTE")

    first = asyncio.run(repository.search("same phrase", technology="LTE", limit=10))
    second = asyncio.run(repository.search("same phrase", technology="LTE", limit=10))

    assert first == second
    assert [item["title"] for item in first] == ["a.md", "b.md"]
    assert asyncio.run(
        repository.search("same phrase", technology="5G_SA", limit=10)
    ) == ()


def test_markdown_repository_skips_unsafe_and_non_markdown_content(tmp_path) -> None:
    (tmp_path / "unsafe.md").write_text(
        "# Subscriber\nIMSI: 208930000000001", encoding="utf-8"
    )
    (tmp_path / "ignored.txt").write_text("ERAB failure", encoding="utf-8")
    repository = MarkdownDocumentRepository(tmp_path)

    assert asyncio.run(repository.search("subscriber ERAB", limit=10)) == ()


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_markdown_repository_bounds_result_limit(tmp_path, limit: int) -> None:
    repository = MarkdownDocumentRepository(tmp_path)

    with pytest.raises(ValueError, match="limit"):
        asyncio.run(repository.search("query", limit=limit))


def test_markdown_repository_bounds_file_count(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_FILES", 2)
    for index in range(3):
        (tmp_path / f"{index}.md").write_text(
            "# LTE\nERAB failure", encoding="utf-8"
        )

    with pytest.raises(ValueError, match="file count"):
        asyncio.run(MarkdownDocumentRepository(tmp_path).search("ERAB"))


def test_markdown_repository_bounds_chunks_per_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_CHUNKS_PER_FILE", 3)
    (tmp_path / "dense.md").write_text(
        "\n".join(f"# Heading {index}\nERAB" for index in range(4)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="chunk count"):
        asyncio.run(MarkdownDocumentRepository(tmp_path).search("ERAB"))


def test_markdown_repository_bounds_total_scanned_bytes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_TOTAL_BYTES", 10)
    (tmp_path / "large-total.md").write_text(
        "# LTE\nERAB failure", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="total byte"):
        asyncio.run(MarkdownDocumentRepository(tmp_path).search("ERAB"))


def test_markdown_repository_bounds_matching_candidates(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_CANDIDATES", 2)
    (tmp_path / "matches.md").write_text(
        "\n".join(f"# Heading {index}\nERAB" for index in range(3)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="candidate count"):
        asyncio.run(MarkdownDocumentRepository(tmp_path).search("ERAB"))


def test_markdown_repository_bounds_query_characters(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_QUERY_CHARS", 10)

    with pytest.raises(ValueError, match="query character"):
        asyncio.run(
            MarkdownDocumentRepository(tmp_path).search("x" * 11)
        )


def test_markdown_repository_bounds_query_terms(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents_module, "MAX_DOCUMENT_QUERY_TERMS", 2)

    with pytest.raises(ValueError, match="query term"):
        asyncio.run(
            MarkdownDocumentRepository(tmp_path).search("one two three")
        )
