"""Strict contracts for answer-blind RCA ranking and sealed evaluation."""

from __future__ import annotations

import hashlib
import math
from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from telco_domain import assert_model_safe

from .evaluation import EvaluationError
from .schema import canonical_json_bytes


RCA_FEATURE_SCHEMA = "networkagent-rca-feature-set/1.0"
RCA_RANKING_SCHEMA = "networkagent-rca-ranking/1.0"
RCA_RANKING_SEAL_SCHEMA = "networkagent-rca-ranking-seal/1.1"
RCA_EVALUATION_SCHEMA = "networkagent-rca-evaluation/1.0"
RCA_RANKING_ALGORITHM = "networkagent-multisource-shift-v1"

MAX_FEATURE_AGGREGATES = 4_096
MAX_FEATURE_CANDIDATES = 64
MAX_FEATURE_SERIALIZED_BYTES = 4 * 1024 * 1024
MAX_RANKING_SERIALIZED_BYTES = 1024 * 1024
MAX_TOTAL_ABSOLUTE = 10**18
MAX_SAMPLE_COUNT = 10**9
PPM_SCALE = 1_000_000
MAX_SHIFT_PPM = 1_000_000_000

CandidateId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        min_length=1,
        max_length=64,
    ),
]
EvidenceId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^rcaevidence-[0-9a-f]{64}$",
        min_length=76,
        max_length=76,
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[0-9a-f]{64}$",
        min_length=64,
        max_length=64,
    ),
]
AggregateTotal = Annotated[
    StrictInt,
    Field(ge=-MAX_TOTAL_ABSOLUTE, le=MAX_TOTAL_ABSOLUTE),
]
PositiveCount = Annotated[StrictInt, Field(ge=1, le=MAX_SAMPLE_COUNT)]
Ppm = Annotated[StrictInt, Field(ge=0, le=PPM_SCALE)]


class _RcaModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
        revalidate_instances="always",
        strict=True,
        str_strip_whitespace=False,
        validate_default=True,
    )

    @model_validator(mode="after")
    def _safe_projection(self) -> Self:
        assert_model_safe(self.model_dump(mode="python"))
        return self


class EvidenceAggregate(_RcaModel):
    """One label-free, integer aggregate for a candidate and modality."""

    evidence_id: EvidenceId
    candidate_id: CandidateId
    modality: Literal["METRIC", "LOG", "TRACE"]
    baseline_total: AggregateTotal
    baseline_count: PositiveCount
    observed_total: AggregateTotal
    observed_count: PositiveCount


class RcaFeatureSet(_RcaModel):
    """Bounded answer-blind aggregates with no evaluation token."""

    schema_version: Literal[RCA_FEATURE_SCHEMA] = RCA_FEATURE_SCHEMA
    aggregates: tuple[EvidenceAggregate, ...] = Field(
        default=(),
        max_length=MAX_FEATURE_AGGREGATES,
    )

    @model_validator(mode="after")
    def _validate_feature_set(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.aggregates)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("feature evidence identifiers must be unique")
        candidate_ids = {item.candidate_id for item in self.aggregates}
        if len(candidate_ids) > MAX_FEATURE_CANDIDATES:
            raise ValueError("feature candidate budget exceeded")
        if len(canonical_json_bytes(self)) > MAX_FEATURE_SERIALIZED_BYTES:
            raise ValueError("feature set serialized budget exceeded")
        return self


class RcaRankedCandidate(_RcaModel):
    candidate_id: CandidateId
    rank: Annotated[StrictInt, Field(ge=1, le=MAX_FEATURE_CANDIDATES)]
    score_numerator: Annotated[StrictInt, Field(ge=1, le=MAX_TOTAL_ABSOLUTE)]
    score_denominator: Annotated[StrictInt, Field(ge=1, le=MAX_TOTAL_ABSOLUTE)]
    evidence_ids: tuple[EvidenceId, ...] = Field(
        min_length=1,
        max_length=MAX_FEATURE_AGGREGATES,
    )

    @model_validator(mode="after")
    def _validate_fraction_and_evidence(self) -> Self:
        if math.gcd(self.score_numerator, self.score_denominator) != 1:
            raise ValueError("ranking score fraction must be reduced")
        if self.evidence_ids != tuple(sorted(set(self.evidence_ids))):
            raise ValueError(
                "ranking evidence identifiers must be unique " "and sorted"
            )
        return self

    @property
    def score(self) -> Fraction:
        return Fraction(self.score_numerator, self.score_denominator)


class RcaRanking(_RcaModel):
    schema_version: Literal[RCA_RANKING_SCHEMA] = RCA_RANKING_SCHEMA
    algorithm: Literal[RCA_RANKING_ALGORITHM] = RCA_RANKING_ALGORITHM
    outcome: Literal["RANKED", "INCONCLUSIVE"]
    candidates: tuple[RcaRankedCandidate, ...] = Field(
        default=(),
        max_length=MAX_FEATURE_CANDIDATES,
    )

    @model_validator(mode="after")
    def _validate_ranking(self) -> Self:
        if (self.outcome == "RANKED") != bool(self.candidates):
            raise ValueError("ranking outcome and candidates are inconsistent")
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("ranked candidates must be unique")
        evidence_id_list: list[str] = []
        for item in self.candidates:
            evidence_id_list.extend(item.evidence_ids)
        evidence_ids = tuple(evidence_id_list)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("ranking evidence references must be unique")
        previous: RcaRankedCandidate | None = None
        for expected_rank, item in enumerate(self.candidates, start=1):
            if item.rank != expected_rank:
                raise ValueError("ranking positions must be contiguous")
            if previous is not None and (
                item.score > previous.score
                or (
                    item.score == previous.score
                    and item.candidate_id <= previous.candidate_id
                )
            ):
                raise ValueError("ranking order is not deterministic")
            previous = item
        if len(self.canonical_bytes()) > MAX_RANKING_SERIALIZED_BYTES:
            raise ValueError("ranking serialized budget exceeded")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class RcaRankingSeal(_RcaModel):
    schema_version: Literal["networkagent-rca-ranking-seal/1.1"] = (
        RCA_RANKING_SEAL_SCHEMA
    )
    feature_sha256: Sha256Digest
    ranking: RcaRanking
    ranking_sha256: Sha256Digest

    @model_validator(mode="after")
    def _validate_seal(self) -> Self:
        expected = hashlib.sha256(self.canonical_bytes()).hexdigest()
        if self.ranking_sha256 != expected:
            message = "ranking seal digest does not match canonical bytes"
            raise ValueError(message)
        return self

    def canonical_bytes(self) -> bytes:
        return self.ranking.canonical_bytes()


class RcaTruth(_RcaModel):
    """Private evaluator-only answer for one externally keyed sample."""

    candidate_id: CandidateId
    valid_evidence_ids: tuple[EvidenceId, ...] = Field(
        default=(),
        max_length=MAX_FEATURE_AGGREGATES,
    )

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        normalized_ids = tuple(sorted(set(self.valid_evidence_ids)))
        if self.valid_evidence_ids != normalized_ids:
            message = "valid evidence identifiers must be unique and sorted"
            raise ValueError(message)
        return self


class RcaEvaluationReport(_RcaModel):
    schema_version: Literal[RCA_EVALUATION_SCHEMA] = RCA_EVALUATION_SCHEMA
    ranking_algorithm: Literal[RCA_RANKING_ALGORITHM] = RCA_RANKING_ALGORITHM
    sample_count: Annotated[StrictInt, Field(ge=1, le=10_000)]
    ranked_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    inconclusive_count: Annotated[StrictInt, Field(ge=0, le=10_000)]
    ac_at_1_ppm: Ppm
    ac_at_2_ppm: Ppm
    ac_at_3_ppm: Ppm
    ac_at_4_ppm: Ppm
    ac_at_5_ppm: Ppm
    avg_at_5_ppm: Ppm
    mrr_ppm: Ppm
    evidence_reference_count: Annotated[StrictInt, Field(ge=0, le=100_000)]
    valid_evidence_reference_count: Annotated[
        StrictInt,
        Field(ge=0, le=100_000),
    ]
    evidence_validity_ppm: Ppm

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if self.ranked_count + self.inconclusive_count != self.sample_count:
            raise ValueError("evaluation outcome counts are inconsistent")
        accuracies = (
            self.ac_at_1_ppm,
            self.ac_at_2_ppm,
            self.ac_at_3_ppm,
            self.ac_at_4_ppm,
            self.ac_at_5_ppm,
        )
        if accuracies != tuple(sorted(accuracies)):
            raise ValueError("cumulative accuracy must be nondecreasing")
        if self.valid_evidence_reference_count > self.evidence_reference_count:
            raise ValueError("valid evidence count exceeds reference count")
        if self.evidence_reference_count == 0:
            if self.evidence_validity_ppm != 0:
                raise ValueError("empty evidence must have zero validity")
        return self


__all__ = [
    "EvaluationError",
    "EvidenceAggregate",
    "MAX_FEATURE_AGGREGATES",
    "MAX_FEATURE_CANDIDATES",
    "MAX_SHIFT_PPM",
    "PPM_SCALE",
    "RCA_EVALUATION_SCHEMA",
    "RCA_FEATURE_SCHEMA",
    "RCA_RANKING_ALGORITHM",
    "RCA_RANKING_SCHEMA",
    "RCA_RANKING_SEAL_SCHEMA",
    "RcaEvaluationReport",
    "RcaFeatureSet",
    "RcaRankedCandidate",
    "RcaRanking",
    "RcaRankingSeal",
    "RcaTruth",
]
