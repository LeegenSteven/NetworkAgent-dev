from __future__ import annotations

from datetime import UTC, datetime

from telco_domain import Incident, IncidentStatus, Technology
from telco_local.similarity import cosine_similarity, rank_similar_incidents, tokenize


NOW = datetime(2025, 11, 24, 18, 30, tzinfo=UTC)


def _incident(
    incident_id: str,
    description: str,
    *,
    root_cause: str | None = None,
    technology: Technology = Technology.LTE,
) -> Incident:
    return Incident(
        incident_id=incident_id,
        trace_id=f"trace-{incident_id}",
        technology=technology,
        status=IncidentStatus.RCA_COMPLETE if root_cause else IncidentStatus.DETECTED,
        description=description,
        root_cause=root_cause,
        detected_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def test_similarity_prefers_related_incident() -> None:
    related = cosine_similarity(
        "ERAB setup failed due to S1 security failure",
        "S1 security setup failure caused ERAB setup errors",
    )
    unrelated = cosine_similarity(
        "ERAB setup failed due to S1 security failure",
        "uplink RSSI configuration adjustment",
    )

    assert related > unrelated
    assert 0 <= related <= 1


def test_tokenize_supports_chinese_and_latin_deterministically() -> None:
    assert tokenize("ERAB 安全配置失败") == (
        "erab",
        "安全",
        "全配",
        "配置",
        "置失",
        "失败",
    )


def test_rank_excludes_current_and_incidents_without_analyzed_history() -> None:
    current = _incident("current", "ERAB S1 安全配置失败")
    related = _incident(
        "related",
        "ERAB 建立失败",
        root_cause="S1 安全配置失败",
    )
    unanalyzed = _incident("new", "ERAB S1 安全配置失败")
    unrelated = _incident(
        "unrelated",
        "上行 RSSI 偏高",
        root_cause="上行功控配置异常",
    )

    ranked = rank_similar_incidents(
        current,
        [current, unrelated, unanalyzed, related],
        min_score=0.2,
        limit=5,
    )

    assert [item.incident_id for item in ranked] == ["related"]
    assert ranked[0].root_cause == "S1 安全配置失败"


def test_rank_uses_incident_id_as_stable_tie_breaker() -> None:
    current = _incident("current", "same words")
    candidates = [
        _incident("b", "same words", root_cause="same cause"),
        _incident("a", "same words", root_cause="same cause"),
    ]

    ranked = rank_similar_incidents(current, candidates, min_score=0, limit=2)

    assert [item.incident_id for item in ranked] == ["a", "b"]


def test_rank_excludes_history_from_another_technology() -> None:
    current = _incident("current", "ERAB S1 security failure")
    lte = _incident(
        "lte",
        "ERAB S1 security failure",
        root_cause="LTE security setup failure",
    )
    five_g = _incident(
        "five-g",
        "ERAB S1 security failure",
        root_cause="5G signaling failure",
        technology=Technology.FIVE_G_SA,
    )

    ranked = rank_similar_incidents(
        current,
        [five_g, lte],
        min_score=0,
        limit=5,
    )

    assert [item.incident_id for item in ranked] == ["lte"]
