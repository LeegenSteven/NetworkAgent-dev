"""Privacy-safe local Markdown search implementing DocumentRepository."""

from __future__ import annotations

import hashlib
import re
from itertools import islice
from pathlib import Path
from urllib.parse import quote

from telco_domain import Technology, assert_model_safe, find_sensitive_paths

from .similarity import cosine_similarity, tokenize


MAX_DOCUMENT_FILE_BYTES = 2_000_000
MAX_DOCUMENT_FILES = 256
MAX_DOCUMENT_TOTAL_BYTES = 16_000_000
MAX_DOCUMENT_CHUNK_CHARS = 3_500
MAX_DOCUMENT_CHUNKS_PER_FILE = 512
MAX_DOCUMENT_CANDIDATES = 5_000
MAX_DOCUMENT_QUERY_CHARS = 4_096
MAX_DOCUMENT_QUERY_TERMS = 512
_SECTION_BOUNDARY = re.compile(r"(?=^#{1,3}\s)", flags=re.MULTILINE)


class DocumentLoadError(ValueError):
    """An approved document directory exceeds a safe, deterministic bound."""


def _document_chunks(content: str) -> tuple[str, ...]:
    chunks: list[str] = []

    def append(section: str) -> None:
        normalized = section.strip()
        if not normalized:
            return
        if len(chunks) >= MAX_DOCUMENT_CHUNKS_PER_FILE:
            raise DocumentLoadError(
                "document chunk count exceeds the per-file limit"
            )
        chunks.append(normalized[:MAX_DOCUMENT_CHUNK_CHARS])

    start = 0
    for boundary in _SECTION_BOUNDARY.finditer(content):
        position = boundary.start()
        if position > start:
            append(content[start:position])
        start = position
    append(content[start:])
    return tuple(chunks)


class MarkdownDocumentRepository:
    """Search an approved directory without exposing host filesystem paths."""

    def __init__(
        self,
        documents_directory: str | Path,
        *,
        technology: str = Technology.LTE.value,
    ) -> None:
        self._documents_directory = Path(documents_directory)
        self._technology = technology

    async def search(
        self,
        query: str,
        *,
        technology: str | None = None,
        limit: int = 10,
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if len(query) > MAX_DOCUMENT_QUERY_CHARS:
            raise ValueError("document query character count exceeds the limit")
        assert_model_safe(query)
        if len(tokenize(query)) > MAX_DOCUMENT_QUERY_TERMS:
            raise ValueError("document query term count exceeds the limit")
        requested_technology = getattr(technology, "value", technology)
        if (
            requested_technology is not None
            and requested_technology != self._technology
        ):
            return ()

        root = self._documents_directory.resolve()
        if not root.is_dir():
            return ()

        paths = list(islice(root.rglob("*.md"), MAX_DOCUMENT_FILES + 1))
        if len(paths) > MAX_DOCUMENT_FILES:
            raise DocumentLoadError("document file count exceeds the limit")

        ranked_candidates: list[tuple[float, str, int, str]] = []
        candidate_count = 0
        total_bytes = 0
        for path in sorted(paths, key=lambda item: item.as_posix()):
            try:
                resolved = path.resolve()
                if path.is_symlink() or not resolved.is_relative_to(root):
                    continue
                file_bytes = path.stat().st_size
                if file_bytes > MAX_DOCUMENT_FILE_BYTES:
                    continue
                total_bytes += file_bytes
                if total_bytes > MAX_DOCUMENT_TOTAL_BYTES:
                    raise DocumentLoadError(
                        "document total byte count exceeds the limit"
                    )
                content = path.read_text(encoding="utf-8")
            except DocumentLoadError:
                raise
            except (OSError, UnicodeError):
                continue
            relative = path.relative_to(root).as_posix()
            for index, chunk in enumerate(_document_chunks(content)):
                if find_sensitive_paths(chunk):
                    continue
                score = cosine_similarity(query, chunk)
                if score <= 0:
                    continue
                candidate_count += 1
                if candidate_count > MAX_DOCUMENT_CANDIDATES:
                    raise DocumentLoadError(
                        "document candidate count exceeds the limit"
                    )
                ranked_candidates.append((score, relative, index, chunk))
                ranked_candidates.sort(
                    key=lambda item: (-item[0], item[1], item[2])
                )
                if len(ranked_candidates) > limit:
                    ranked_candidates.pop()

        results: list[dict[str, object]] = []
        for score, relative, index, chunk in ranked_candidates:
            digest = hashlib.sha256(
                f"{relative}\0{index}\0{chunk}".encode("utf-8")
            ).hexdigest()
            results.append(
                {
                    "document_id": f"doc-{digest[:32]}",
                    "uri": f"document://local/{quote(relative)}#chunk={index}",
                    "title": Path(relative).name,
                    "excerpt": chunk,
                    "score": round(score, 6),
                    "technology": self._technology,
                }
            )

        result = tuple(results)
        assert_model_safe(result)
        return result


__all__ = [
    "DocumentLoadError",
    "MAX_DOCUMENT_CANDIDATES",
    "MAX_DOCUMENT_CHUNK_CHARS",
    "MAX_DOCUMENT_CHUNKS_PER_FILE",
    "MAX_DOCUMENT_FILE_BYTES",
    "MAX_DOCUMENT_FILES",
    "MAX_DOCUMENT_QUERY_CHARS",
    "MAX_DOCUMENT_QUERY_TERMS",
    "MAX_DOCUMENT_TOTAL_BYTES",
    "MarkdownDocumentRepository",
]
