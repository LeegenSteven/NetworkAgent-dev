"""Immutable pre-reveal commitments for one private RCAEval batch."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from itertools import islice
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)

from .adapters import AdapterError
from .rcaeval_models import (
    MAX_FEATURE_AGGREGATES,
    MAX_FEATURE_CANDIDATES,
    RCA_RANKING_ALGORITHM,
    EvidenceAggregate,
    RcaFeatureSet,
    RcaRankedCandidate,
    RcaRanking,
    RcaRankingSeal,
)
from .schema import canonical_json_bytes


RCA_RANKING_BATCH_COMMITMENT_SCHEMA = (
    "networkagent-rcaeval-ranking-batch-commitment/1.0"
)
RCA_RANKING_BATCH_SIZE = 5
RCA_ARTIFACT_CLOSURE_COUNT = 16
MAX_COMMITMENT_MAPPING_ITEMS = RCA_RANKING_BATCH_SIZE
MAX_CASE_KEY_BYTES = 256

_CASE_KEY_DOMAIN = b"networkagent-rcaeval-case-key-v1\0"
_CASE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}\Z", re.ASCII)
_CASE_KEY_SHA256 = re.compile(r"^[0-9a-f]{64}\Z", re.ASCII)
_SLOT = re.compile(r"^rcaslot-[0-9a-f]{64}\Z", re.ASCII)

_Identifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
_SemanticVersion = Annotated[
    str,
    StringConstraints(
        strict=True,
        max_length=64,
        pattern=(
            r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|"
            r"[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)(?:\."
            r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-]"
            r"[0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+"
            r"(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
]
_LockId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=72,
        max_length=72,
        pattern=r"^lablock-[0-9a-f]{64}$",
    ),
]
_Sha256 = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
_OpaqueSlot = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=72,
        max_length=72,
        pattern=r"^rcaslot-[0-9a-f]{64}$",
    ),
]


def _invalid() -> AdapterError:
    return AdapterError("adapter_invalid_input")


def _limit() -> AdapterError:
    return AdapterError("adapter_limit_exceeded")


def _detached(error: AdapterError) -> AdapterError:
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = False
    error.__traceback__ = None
    return error


class _CommitmentModel(BaseModel):
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


class RcaRankingBatchCommitmentItem(_CommitmentModel):
    """One opaque slot's feature and ranking digests."""

    opaque_slot: _OpaqueSlot
    case_key_sha256: _Sha256
    feature_sha256: _Sha256
    ranking_sha256: _Sha256


class RcaRankingBatchCommitment(_CommitmentModel):
    """Canonical immutable seal over one five-slot answer-blind batch."""

    schema_version: Literal["networkagent-rcaeval-ranking-batch-commitment/1.0"] = (
        RCA_RANKING_BATCH_COMMITMENT_SCHEMA
    )
    catalog_id: _Identifier
    catalog_version: _SemanticVersion
    dataset_id: _Identifier
    dataset_version: _Identifier
    lock_id: _LockId
    artifact_closure_count: Annotated[StrictInt, Field(ge=16, le=16)] = (
        RCA_ARTIFACT_CLOSURE_COUNT
    )
    artifact_closure_sha256: _Sha256
    ranking_algorithm: Literal["networkagent-multisource-shift-v1"] = (
        RCA_RANKING_ALGORITHM
    )
    items: tuple[RcaRankingBatchCommitmentItem, ...] = Field(
        min_length=RCA_RANKING_BATCH_SIZE,
        max_length=RCA_RANKING_BATCH_SIZE,
    )
    externally_timestamped: StrictBool = False
    commitment_sha256: _Sha256

    @model_validator(mode="after")
    def _validate_commitment(self) -> Self:
        slots = tuple(item.opaque_slot for item in self.items)
        if slots != tuple(sorted(set(slots))):
            raise ValueError("commitment items must be unique and sorted")
        if self.externally_timestamped is not False:
            raise ValueError("commitment must not claim external timestamping")
        expected = hashlib.sha256(self.canonical_body_bytes()).hexdigest()
        if self.commitment_sha256 != expected:
            raise ValueError("commitment digest does not match canonical body")
        return self

    def canonical_body_bytes(self) -> bytes:
        """Return canonical bytes covered by ``commitment_sha256``."""

        return canonical_json_bytes(
            self.model_dump(
                mode="python",
                exclude={"commitment_sha256"},
            )
        )


def case_key_sha256(case_key: str) -> str:
    """Hash one private case key into its domain-separated index token."""

    try:
        if (
            type(case_key) is not str
            or len(case_key) > MAX_CASE_KEY_BYTES
            or _CASE_KEY.fullmatch(case_key) is None
        ):
            raise _invalid()
        encoded = case_key.encode("utf-8")
        if not encoded or len(encoded) > MAX_CASE_KEY_BYTES:
            raise _invalid()
        return hashlib.sha256(_CASE_KEY_DOMAIN + encoded).hexdigest()
    except AdapterError:
        failure = _invalid()
    except Exception:
        failure = _invalid()
    raise _detached(failure)


def _copied_mapping_items(value: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, Mapping):
        raise _invalid()
    try:
        count = len(value)
    except Exception:
        raise _invalid() from None
    if type(count) is not int or count < 0:
        raise _invalid()
    if count > MAX_COMMITMENT_MAPPING_ITEMS:
        raise _limit()
    if count != RCA_RANKING_BATCH_SIZE:
        raise _invalid()
    try:
        keys = tuple(islice(iter(value), count + 1))
    except Exception:
        raise _invalid() from None
    if len(keys) != count:
        raise _invalid()
    if any(type(key) is not str or _SLOT.fullmatch(key) is None for key in keys):
        raise _invalid()
    if len(keys) != len(set(keys)):
        raise _invalid()
    try:
        copied = tuple((key, value[key]) for key in keys)
    except Exception:
        raise _invalid() from None
    return tuple(sorted(copied, key=lambda item: item[0]))


def _validated_feature(value: object) -> RcaFeatureSet:
    if type(value) is not RcaFeatureSet:
        raise _invalid()
    try:
        aggregates = value.aggregates
        if type(aggregates) is not tuple:
            raise _invalid()
        if len(aggregates) > MAX_FEATURE_AGGREGATES:
            raise _limit()
        if any(type(item) is not EvidenceAggregate for item in aggregates):
            raise _invalid()
        return RcaFeatureSet.model_validate(
            value.model_dump(mode="python"),
            strict=True,
        )
    except AdapterError:
        raise
    except Exception:
        raise _invalid() from None


def _validated_seal(value: object) -> RcaRankingSeal:
    if type(value) is not RcaRankingSeal:
        raise _invalid()
    try:
        ranking = value.ranking
        if type(ranking) is not RcaRanking:
            raise _invalid()
        candidates = ranking.candidates
        if type(candidates) is not tuple:
            raise _invalid()
        if len(candidates) > MAX_FEATURE_CANDIDATES:
            raise _limit()
        evidence_count = 0
        for candidate in candidates:
            if type(candidate) is not RcaRankedCandidate:
                raise _invalid()
            evidence_ids = candidate.evidence_ids
            if type(evidence_ids) is not tuple:
                raise _invalid()
            evidence_count += len(evidence_ids)
            if evidence_count > MAX_FEATURE_AGGREGATES:
                raise _limit()
        return RcaRankingSeal.model_validate(
            value.model_dump(mode="python"),
            strict=True,
        )
    except AdapterError:
        raise
    except Exception:
        raise _invalid() from None


def _validated_batch(
    case_key_sha256_by_slot: object,
    features_by_slot: object,
    sealed_rankings: object,
) -> tuple[tuple[str, str, RcaFeatureSet, RcaRankingSeal], ...]:
    case_items = _copied_mapping_items(case_key_sha256_by_slot)
    feature_items = _copied_mapping_items(features_by_slot)
    seal_items = _copied_mapping_items(sealed_rankings)
    case_slots = tuple(slot for slot, _ in case_items)
    feature_slots = tuple(slot for slot, _ in feature_items)
    seal_slots = tuple(slot for slot, _ in seal_items)
    if case_slots != feature_slots or feature_slots != seal_slots:
        raise _invalid()
    if any(
        type(digest) is not str or _CASE_KEY_SHA256.fullmatch(digest) is None
        for _slot, digest in case_items
    ):
        raise _invalid()
    if len({digest for _slot, digest in case_items}) != RCA_RANKING_BATCH_SIZE:
        raise _invalid()

    normalized: list[tuple[str, str, RcaFeatureSet, RcaRankingSeal]] = []
    for (
        (slot, case_key_digest),
        (_feature_slot, feature_value),
        (_seal_slot, seal_value),
    ) in zip(
        case_items,
        feature_items,
        seal_items,
        strict=True,
    ):
        feature = _validated_feature(feature_value)
        seal = _validated_seal(seal_value)
        feature_digest = hashlib.sha256(canonical_json_bytes(feature)).hexdigest()
        if seal.feature_sha256 != feature_digest:
            raise _invalid()
        normalized.append((slot, case_key_digest, feature, seal))
    return tuple(normalized)


def _validated_commitment(
    value: object,
) -> RcaRankingBatchCommitment:
    if type(value) is not RcaRankingBatchCommitment:
        raise _invalid()
    try:
        items = value.items
        if type(items) is not tuple:
            raise _invalid()
        if len(items) > RCA_RANKING_BATCH_SIZE:
            raise _limit()
        if len(items) != RCA_RANKING_BATCH_SIZE:
            raise _invalid()
        if any(type(item) is not RcaRankingBatchCommitmentItem for item in items):
            raise _invalid()
        normalized_items = tuple(
            RcaRankingBatchCommitmentItem.model_validate(
                {
                    "opaque_slot": item.opaque_slot,
                    "case_key_sha256": item.case_key_sha256,
                    "feature_sha256": item.feature_sha256,
                    "ranking_sha256": item.ranking_sha256,
                },
                strict=True,
            )
            for item in items
        )
        return RcaRankingBatchCommitment.model_validate(
            {
                "schema_version": value.schema_version,
                "catalog_id": value.catalog_id,
                "catalog_version": value.catalog_version,
                "dataset_id": value.dataset_id,
                "dataset_version": value.dataset_version,
                "lock_id": value.lock_id,
                "artifact_closure_count": value.artifact_closure_count,
                "artifact_closure_sha256": value.artifact_closure_sha256,
                "ranking_algorithm": value.ranking_algorithm,
                "items": normalized_items,
                "externally_timestamped": value.externally_timestamped,
                "commitment_sha256": value.commitment_sha256,
            },
            strict=True,
        )
    except AdapterError:
        raise
    except Exception:
        raise _invalid() from None


def _body(
    *,
    catalog_id: object,
    catalog_version: object,
    dataset_id: object,
    dataset_version: object,
    lock_id: object,
    artifact_closure_count: object,
    artifact_closure_sha256: object,
    ranking_algorithm: object,
    externally_timestamped: object,
    batch: tuple[tuple[str, str, RcaFeatureSet, RcaRankingSeal], ...],
) -> dict[str, object]:
    items = tuple(
        RcaRankingBatchCommitmentItem(
            opaque_slot=slot,
            case_key_sha256=case_key_digest,
            feature_sha256=hashlib.sha256(canonical_json_bytes(feature)).hexdigest(),
            ranking_sha256=seal.ranking_sha256,
        )
        for slot, case_key_digest, feature, seal in batch
    )
    return {
        "schema_version": RCA_RANKING_BATCH_COMMITMENT_SCHEMA,
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "lock_id": lock_id,
        "artifact_closure_count": artifact_closure_count,
        "artifact_closure_sha256": artifact_closure_sha256,
        "ranking_algorithm": ranking_algorithm,
        "items": items,
        "externally_timestamped": externally_timestamped,
    }


def create_ranking_batch_commitment(
    *,
    catalog_id: str,
    catalog_version: str,
    dataset_id: str,
    dataset_version: str,
    lock_id: str,
    artifact_closure_sha256: str,
    case_key_sha256_by_slot: Mapping[str, str],
    features_by_slot: Mapping[str, RcaFeatureSet],
    sealed_rankings: Mapping[str, RcaRankingSeal],
    artifact_closure_count: int = RCA_ARTIFACT_CLOSURE_COUNT,
    ranking_algorithm: str = RCA_RANKING_ALGORITHM,
    externally_timestamped: bool = False,
) -> RcaRankingBatchCommitment:
    """Create the canonical commitment after strict batch reverification."""

    try:
        batch = _validated_batch(
            case_key_sha256_by_slot,
            features_by_slot,
            sealed_rankings,
        )
        body = _body(
            catalog_id=catalog_id,
            catalog_version=catalog_version,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            lock_id=lock_id,
            artifact_closure_count=artifact_closure_count,
            artifact_closure_sha256=artifact_closure_sha256,
            ranking_algorithm=ranking_algorithm,
            externally_timestamped=externally_timestamped,
            batch=batch,
        )
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        return RcaRankingBatchCommitment(
            **body,
            commitment_sha256=digest,
        )
    except AdapterError as error:
        failure = error
    except Exception:
        failure = _invalid()
    raise _detached(failure)


def verify_ranking_batch_commitment(
    commitment: RcaRankingBatchCommitment,
    *,
    case_key_sha256_by_slot: Mapping[str, str],
    features_by_slot: Mapping[str, RcaFeatureSet],
    sealed_rankings: Mapping[str, RcaRankingSeal],
) -> RcaRankingBatchCommitment:
    """Revalidate a commitment and its complete feature/ranking batch."""

    try:
        normalized = _validated_commitment(commitment)
        expected = create_ranking_batch_commitment(
            catalog_id=normalized.catalog_id,
            catalog_version=normalized.catalog_version,
            dataset_id=normalized.dataset_id,
            dataset_version=normalized.dataset_version,
            lock_id=normalized.lock_id,
            artifact_closure_count=normalized.artifact_closure_count,
            artifact_closure_sha256=normalized.artifact_closure_sha256,
            ranking_algorithm=normalized.ranking_algorithm,
            externally_timestamped=normalized.externally_timestamped,
            case_key_sha256_by_slot=case_key_sha256_by_slot,
            features_by_slot=features_by_slot,
            sealed_rankings=sealed_rankings,
        )
        if canonical_json_bytes(expected) != canonical_json_bytes(normalized):
            raise _invalid()
        return normalized
    except AdapterError as error:
        failure = error
    except Exception:
        failure = _invalid()
    raise _detached(failure)


__all__ = [
    "MAX_COMMITMENT_MAPPING_ITEMS",
    "RCA_ARTIFACT_CLOSURE_COUNT",
    "RCA_RANKING_BATCH_COMMITMENT_SCHEMA",
    "RCA_RANKING_BATCH_SIZE",
    "RcaRankingBatchCommitment",
    "RcaRankingBatchCommitmentItem",
    "case_key_sha256",
    "create_ranking_batch_commitment",
    "verify_ranking_batch_commitment",
]
