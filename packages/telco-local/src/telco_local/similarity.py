"""Dependency-free, deterministic lexical similarity for local incidents."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from telco_domain import Incident, assert_model_safe


_LATIN_TOKEN = re.compile(r"[a-z0-9_]+")
_CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")


class SimilarIncident(BaseModel):
    """A bounded, privacy-safe historical match used by RCA reporting."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    incident_id: str = Field(min_length=1, max_length=256)
    similarity: float = Field(ge=0, le=1)
    summary: str = Field(default="", max_length=1_024)
    root_cause: str | None = Field(default=None, max_length=1_024)


def tokenize(text: str) -> tuple[str, ...]:
    """Tokenize Latin identifiers and Chinese bigrams without locale state."""

    normalized = text.lower()
    tokens: list[str] = _LATIN_TOKEN.findall(normalized)
    for run in _CHINESE_RUN.findall(normalized):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(tokens)


def cosine_similarity(left: str, right: str) -> float:
    """Return stable bag-of-token cosine similarity in the closed interval."""

    left_counts = Counter(tokenize(left))
    right_counts = Counter(tokenize(right))
    if not left_counts or not right_counts:
        return 0.0
    dot = sum(value * right_counts.get(token, 0) for token, value in left_counts.items())
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _incident_text(incident: Incident) -> str:
    latest_report = max(
        incident.rca_reports,
        key=lambda report: report.version,
        default=None,
    )
    parts = [
        incident.title,
        incident.description,
        incident.root_cause or "",
        " ".join(incident.hypotheses),
        " ".join(item.kpi_name for item in incident.violated_kpis),
    ]
    if latest_report is not None:
        parts.extend(
            [
                latest_report.title or "",
                latest_report.summary,
                latest_report.root_cause or "",
                " ".join(latest_report.hypotheses),
            ]
        )
    return "\n".join(part for part in parts if part)


def rank_similar_incidents(
    incident: Incident,
    candidates: Iterable[Incident],
    *,
    limit: int = 5,
    min_score: float = 0.1,
) -> tuple[SimilarIncident, ...]:
    """Rank analyzed history, excluding the incident being investigated."""

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not 0 <= min_score <= 1 or not math.isfinite(min_score):
        raise ValueError("min_score must be between 0 and 1")
    assert_model_safe(incident)

    query = _incident_text(incident)
    scored: list[SimilarIncident] = []
    for candidate in candidates:
        if candidate.incident_id == incident.incident_id:
            continue
        if candidate.technology is not incident.technology:
            continue
        if not candidate.root_cause and not candidate.rca_reports:
            continue
        assert_model_safe(candidate)
        score = cosine_similarity(query, _incident_text(candidate))
        if score < min_score:
            continue
        latest_report = max(
            candidate.rca_reports,
            key=lambda report: report.version,
            default=None,
        )
        root_cause = candidate.root_cause or (
            latest_report.root_cause if latest_report is not None else None
        )
        scored.append(
            SimilarIncident(
                incident_id=candidate.incident_id,
                similarity=round(score, 6),
                summary=(candidate.description or candidate.title)[:1_024],
                root_cause=(root_cause or "")[:1_024] or None,
            )
        )

    result = tuple(
        sorted(scored, key=lambda item: (-item.similarity, item.incident_id))[:limit]
    )
    assert_model_safe(result)
    return result


__all__ = [
    "SimilarIncident",
    "cosine_similarity",
    "rank_similar_incidents",
    "tokenize",
]
