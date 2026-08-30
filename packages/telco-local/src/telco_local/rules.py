"""Strict, versioned RCA rules shared by the Local Detector and Resolver."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from itertools import islice
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from telco_domain import (
    EvidenceType,
    Incident,
    IncidentSeverity,
    KpiComparator,
    KpiViolation,
    SensitiveDataError,
    Technology,
    assert_model_safe,
)


RULE_SCHEMA_VERSION = "1.0"
BUBBLERAN_REPLAY_DETECTOR_ALGORITHM = (
    "deterministic-bubbleran-replay-threshold-v1"
)
BUBBLERAN_REPLAY_RULE_ID = (
    "5g-sa.bubbleran.persistent-interference.ul-bler"
)
MAX_RULE_FILE_BYTES = 1_000_000
MAX_RULE_FILES = 256
MAX_RULE_TOTAL_BYTES = 16_000_000
_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_FACT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RuleLoadError(ValueError):
    """A local rule set is malformed, ambiguous, or unsafe to load."""


class _DuplicateJsonKeyError(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


class _RuleModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class RuleDetection(_RuleModel):
    """A deterministic KPI threshold and episode-gap definition."""

    kpi_name: str = Field(min_length=1, max_length=256)
    comparator: KpiComparator
    threshold: float
    unit: str | None = Field(default=None, max_length=128)
    max_gap_minutes: int = Field(ge=0, le=10_080)


class FactPredicate(_RuleModel):
    """One comparison against a named, privacy-safe aggregate fact."""

    fact: str = Field(min_length=1, max_length=128)
    comparator: KpiComparator
    value: bool | int | float | str

    @field_validator("fact")
    @classmethod
    def validate_fact_name(cls, value: str) -> str:
        if not _FACT_NAME.fullmatch(value):
            raise ValueError("fact must be a stable lower-case identifier")
        return value

    @field_validator("value")
    @classmethod
    def validate_scalar(cls, value: bool | int | float | str):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("predicate values must be finite")
        if isinstance(value, str) and not value:
            raise ValueError("predicate string values must not be empty")
        return value


class PredicateGroup(_RuleModel):
    """A shallow expression whose evaluation order is stable and inspectable."""

    operator: Literal["ALL", "ANY"] = "ALL"
    predicates: tuple[FactPredicate, ...] = Field(min_length=1, max_length=32)


class RuleAnalysis(_RuleModel):
    """Evidence requirements and deterministic Chinese conclusion templates."""

    evidence_types: tuple[EvidenceType, ...] = Field(min_length=1, max_length=8)
    when: PredicateGroup
    hypothesis_zh: str = Field(min_length=1, max_length=2_048)
    root_cause_zh: str = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_evidence_types(self) -> "RuleAnalysis":
        if len(self.evidence_types) != len(set(self.evidence_types)):
            raise ValueError("analysis evidence_types must not contain duplicates")
        unsupported = set(self.evidence_types) - {
            EvidenceType.METRIC,
            EvidenceType.TRACE,
        }
        if unsupported:
            raise ValueError("analysis evidence_types must be METRIC or TRACE")
        return self


class SeverityCase(_RuleModel):
    """The first matching case within one rule determines its severity."""

    when: PredicateGroup
    severity: IncidentSeverity

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: IncidentSeverity) -> IncidentSeverity:
        if value is IncidentSeverity.UNKNOWN:
            raise ValueError("severity cases must be determinate")
        return value


class RuleSeverity(_RuleModel):
    """Ordered severity cases with a mandatory deterministic fallback."""

    cases: tuple[SeverityCase, ...] = Field(default=(), max_length=32)
    default: IncidentSeverity

    @field_validator("default")
    @classmethod
    def validate_default(cls, value: IncidentSeverity) -> IncidentSeverity:
        if value is IncidentSeverity.UNKNOWN:
            raise ValueError("severity default must be determinate")
        return value


class RcaRule(_RuleModel):
    """One versioned LTE or controlled 5G replay rule without executable prompts."""

    schema_version: Literal["1.0"] = RULE_SCHEMA_VERSION
    rule_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    technology: Literal["LTE", "5G_SA"]
    is_current: StrictBool
    description_zh: str = Field(min_length=1, max_length=4_096)
    detection: RuleDetection
    analysis: RuleAnalysis
    severity: RuleSeverity

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        if not _RULE_ID.fullmatch(value):
            raise ValueError("rule_id must be a stable lower-case identifier")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("version must use semantic versioning")
        return value


def rule_content_sha256(rule: RcaRule) -> str:
    """Return the canonical content identity used by Detector v3 snapshots."""

    canonical = json.dumps(
        (
            "rca-rule-content-v1",
            rule.model_dump(mode="json", round_trip=True),
        ),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class RuleResolutionStatus(StrEnum):
    """Stable provenance outcomes surfaced in deterministic RCA metadata."""

    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"
    CONFLICT = "CONFLICT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NO_VIOLATIONS = "NO_VIOLATIONS"


class RuleResolutionIssueCode(StrEnum):
    """Machine-readable reasons why one claimed violation was not trusted."""

    LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"
    PARTIAL_PROVENANCE = "PARTIAL_PROVENANCE"
    INCIDENT_VERSION_MISSING = "INCIDENT_VERSION_MISSING"
    INCIDENT_VERSION_CONFLICT = "INCIDENT_VERSION_CONFLICT"
    RULE_VERSION_NOT_FOUND = "RULE_VERSION_NOT_FOUND"
    DUPLICATE_RULE_VERSION = "DUPLICATE_RULE_VERSION"
    DETECTOR_ALGORITHM_MISMATCH = "DETECTOR_ALGORITHM_MISMATCH"
    KPI_MISMATCH = "KPI_MISMATCH"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    THRESHOLD_MISMATCH = "THRESHOLD_MISMATCH"
    COMPARATOR_MISMATCH = "COMPARATOR_MISMATCH"
    RULE_CONTENT_HASH_MISSING = "RULE_CONTENT_HASH_MISSING"
    RULE_CONTENT_HASH_INVALID = "RULE_CONTENT_HASH_INVALID"
    RULE_CONTENT_HASH_AMBIGUOUS = "RULE_CONTENT_HASH_AMBIGUOUS"
    RULE_CONTENT_MISMATCH = "RULE_CONTENT_MISMATCH"
    UNREFERENCED_RULE_CONTENT_HASH = "UNREFERENCED_RULE_CONTENT_HASH"
    OBSERVATION_NOT_VIOLATION = "OBSERVATION_NOT_VIOLATION"
    UNREFERENCED_INCIDENT_RULE = "UNREFERENCED_INCIDENT_RULE"


class RuleResolutionIssue(_RuleModel):
    """One bounded provenance failure without free-form or source-file data."""

    violation_key: str = Field(min_length=1, max_length=256)
    code: RuleResolutionIssueCode
    rule_id: str | None = Field(default=None, max_length=128)
    rule_version: str | None = Field(default=None, max_length=64)


class RuleResolution(_RuleModel):
    """Exact rules and stable issues for one immutable Incident snapshot."""

    status: RuleResolutionStatus
    rules: tuple[RcaRule, ...] = ()
    issues: tuple[RuleResolutionIssue, ...] = ()


def compare_values(
    left: object,
    comparator: KpiComparator,
    right: object,
) -> bool:
    """Compare two scalar facts without Python's bool/number coercion traps."""

    if comparator in {
        KpiComparator.LT,
        KpiComparator.LTE,
        KpiComparator.GT,
        KpiComparator.GTE,
    }:
        if (
            isinstance(left, bool)
            or isinstance(right, bool)
            or not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
        ):
            return False
        if not math.isfinite(float(left)) or not math.isfinite(float(right)):
            return False
        if comparator is KpiComparator.LT:
            return left < right
        if comparator is KpiComparator.LTE:
            return left <= right
        if comparator is KpiComparator.GT:
            return left > right
        return left >= right

    if type(left) is not type(right):
        return False
    if comparator is KpiComparator.EQ:
        return left == right
    if comparator is KpiComparator.NE:
        return left != right
    return False


def detection_matches(rule: RcaRule, violation: KpiViolation) -> bool:
    """Return whether a violation is exactly bound to this rule version."""

    return (
        violation.rule_id == rule.rule_id
        and violation.rule_version == rule.version
        and violation.kpi_name == rule.detection.kpi_name
        and violation.unit == rule.detection.unit
        and violation.threshold_value == rule.detection.threshold
        and violation.comparator is rule.detection.comparator
        and compare_values(
            violation.observed_value,
            rule.detection.comparator,
            rule.detection.threshold,
        )
    )


def resolve_rules_for_incident(
    incident: Incident,
    available_rules: tuple[RcaRule, ...] | list[RcaRule],
) -> RuleResolution:
    """Resolve only rule versions cryptographically named by an Incident.

    Current rules are never guessed for legacy or conflicting snapshots.  The
    Detector owns current-rule selection before Incident creation; RCA consumes
    only the immutable provenance recorded on that Incident.
    """

    if incident.technology not in {
        Technology.LTE,
        Technology.FIVE_G_SA,
    }:
        return RuleResolution(status=RuleResolutionStatus.NOT_APPLICABLE)

    by_identity: dict[tuple[str, str], RcaRule] = {}
    duplicate_identities: set[tuple[str, str]] = set()
    for rule in (
        candidate
        for candidate in available_rules
        if candidate.technology == incident.technology.value
    ):
        identity = (rule.rule_id, rule.version)
        previous = by_identity.get(identity)
        if previous is not None and previous != rule:
            duplicate_identities.add(identity)
            continue
        by_identity[identity] = rule

    issues: list[RuleResolutionIssue] = []
    resolved: dict[tuple[str, str], RcaRule] = {}
    referenced_rule_ids: set[str] = set()
    claimed_rule_identities = frozenset(
        (violation.rule_id, violation.rule_version)
        for violation in incident.violated_kpis
        if violation.rule_id is not None and violation.rule_version is not None
    )
    claimed_rule_ids = frozenset(
        rule_id for rule_id, _rule_version in claimed_rule_identities
    )
    raw_content_hashes = incident.model_metadata.get("rule_content_hashes")
    legacy_content_digest = incident.model_metadata.get(
        "rule_content_sha256"
    )
    detector_algorithm = incident.model_metadata.get("detector_algorithm")
    requires_content_digest = (
        detector_algorithm == "deterministic-threshold-episodes-v3"
    )
    content_hashes: Mapping[str, object] = {}
    content_hashes_issue: RuleResolutionIssueCode | None = None
    if raw_content_hashes is not None:
        if legacy_content_digest is not None or not isinstance(
            raw_content_hashes, Mapping
        ):
            content_hashes_issue = (
                RuleResolutionIssueCode.RULE_CONTENT_HASH_INVALID
            )
        else:
            content_hashes = raw_content_hashes
    elif legacy_content_digest is not None:
        if len(claimed_rule_ids) != 1:
            content_hashes_issue = (
                RuleResolutionIssueCode.RULE_CONTENT_HASH_AMBIGUOUS
            )
        else:
            content_hashes = {
                next(iter(claimed_rule_ids)): legacy_content_digest
            }

    def add_issue(
        index: int,
        code: RuleResolutionIssueCode,
        *,
        rule_id: str | None,
        rule_version: str | None,
    ) -> None:
        issues.append(
            RuleResolutionIssue(
                violation_key=f"violation-index-{index}",
                code=code,
                rule_id=rule_id,
                rule_version=rule_version,
            )
        )

    for index, violation in enumerate(incident.violated_kpis):
        rule_id = violation.rule_id
        rule_version = violation.rule_version
        if rule_id is not None:
            referenced_rule_ids.add(rule_id)

        if rule_id is None and rule_version is None:
            add_issue(
                index,
                RuleResolutionIssueCode.LEGACY_UNVERSIONED,
                rule_id=None,
                rule_version=None,
            )
            continue
        if rule_id is None or rule_version is None:
            add_issue(
                index,
                RuleResolutionIssueCode.PARTIAL_PROVENANCE,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue

        incident_version = incident.rule_versions.get(rule_id)
        if incident_version is None:
            add_issue(
                index,
                RuleResolutionIssueCode.INCIDENT_VERSION_MISSING,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if incident_version != rule_version:
            add_issue(
                index,
                RuleResolutionIssueCode.INCIDENT_VERSION_CONFLICT,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue

        is_bubbleran_replay_violation = (
            rule_id == BUBBLERAN_REPLAY_RULE_ID
        )
        if is_bubbleran_replay_violation and (
            detector_algorithm != BUBBLERAN_REPLAY_DETECTOR_ALGORITHM
        ):
            add_issue(
                index,
                RuleResolutionIssueCode.DETECTOR_ALGORITHM_MISMATCH,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue

        identity = (rule_id, rule_version)
        if identity in duplicate_identities:
            add_issue(
                index,
                RuleResolutionIssueCode.DUPLICATE_RULE_VERSION,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        rule = by_identity.get(identity)
        if rule is None:
            add_issue(
                index,
                RuleResolutionIssueCode.RULE_VERSION_NOT_FOUND,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if violation.kpi_name != rule.detection.kpi_name:
            add_issue(
                index,
                RuleResolutionIssueCode.KPI_MISMATCH,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if violation.unit != rule.detection.unit:
            add_issue(
                index,
                RuleResolutionIssueCode.UNIT_MISMATCH,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if violation.threshold_value != rule.detection.threshold:
            add_issue(
                index,
                RuleResolutionIssueCode.THRESHOLD_MISMATCH,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if violation.comparator is not rule.detection.comparator:
            add_issue(
                index,
                RuleResolutionIssueCode.COMPARATOR_MISMATCH,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        content_issue: RuleResolutionIssueCode | None = None
        claimed_content_digest = content_hashes.get(rule_id)
        if (
            is_bubbleran_replay_violation
            and legacy_content_digest is not None
        ):
            content_issue = RuleResolutionIssueCode.RULE_CONTENT_HASH_INVALID
        elif content_hashes_issue is not None:
            content_issue = content_hashes_issue
        elif claimed_content_digest is None:
            if (
                requires_content_digest
                or is_bubbleran_replay_violation
                or raw_content_hashes is not None
            ):
                content_issue = (
                    RuleResolutionIssueCode.RULE_CONTENT_HASH_MISSING
                )
        elif not (
            isinstance(claimed_content_digest, str)
            and _SHA256.fullmatch(claimed_content_digest.lower())
        ):
            content_issue = RuleResolutionIssueCode.RULE_CONTENT_HASH_INVALID
        elif rule_content_sha256(rule) != claimed_content_digest.lower():
            content_issue = RuleResolutionIssueCode.RULE_CONTENT_MISMATCH
        if content_issue is not None:
            add_issue(
                index,
                content_issue,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        if not detection_matches(rule, violation):
            add_issue(
                index,
                RuleResolutionIssueCode.OBSERVATION_NOT_VIOLATION,
                rule_id=rule_id,
                rule_version=rule_version,
            )
            continue
        resolved[identity] = rule

    for index, (rule_id, rule_version) in enumerate(
        sorted(incident.rule_versions.items()),
        start=len(incident.violated_kpis),
    ):
        if rule_id in referenced_rule_ids:
            continue
        add_issue(
            index,
            RuleResolutionIssueCode.UNREFERENCED_INCIDENT_RULE,
            rule_id=rule_id,
            rule_version=rule_version,
        )

    if isinstance(raw_content_hashes, Mapping):
        for index, rule_id in enumerate(
            sorted(set(raw_content_hashes) - claimed_rule_ids),
            start=len(incident.violated_kpis) + len(incident.rule_versions),
        ):
            add_issue(
                index,
                RuleResolutionIssueCode.UNREFERENCED_RULE_CONTENT_HASH,
                rule_id=str(rule_id),
                rule_version=incident.rule_versions.get(str(rule_id)),
            )

    resolved_rules = tuple(
        resolved[identity] for identity in sorted(resolved)
    )
    stable_issues = tuple(
        sorted(
            issues,
            key=lambda item: (
                item.violation_key,
                item.code.value,
                item.rule_id or "",
                item.rule_version or "",
            ),
        )
    )
    if stable_issues:
        if resolved_rules:
            status = RuleResolutionStatus.PARTIAL
        elif all(
            item.code is RuleResolutionIssueCode.LEGACY_UNVERSIONED
            for item in stable_issues
        ):
            status = RuleResolutionStatus.LEGACY_UNVERSIONED
        else:
            status = RuleResolutionStatus.CONFLICT
    elif incident.violated_kpis:
        status = RuleResolutionStatus.EXACT
    else:
        status = RuleResolutionStatus.NO_VIOLATIONS
    return RuleResolution(
        status=status,
        rules=resolved_rules,
        issues=stable_issues,
    )


class JsonRuleRepository:
    """Load strict JSON rule documents and implement the P1 RuleRepository."""

    def __init__(self, rules_directory: str | Path) -> None:
        self._rules_directory = Path(rules_directory)

    def load_all_versions(self) -> tuple[RcaRule, ...]:
        """Validate and return current plus historical versions stably."""

        root = self._rules_directory.resolve()
        if not root.is_dir():
            raise RuleLoadError("rule directory does not exist")

        paths = list(islice(root.glob("*.json"), MAX_RULE_FILES + 1))
        if len(paths) > MAX_RULE_FILES:
            raise RuleLoadError("rule file count exceeds the limit")

        loaded: list[RcaRule] = []
        total_bytes = 0
        for index, path in enumerate(sorted(paths, key=lambda p: p.name)):
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise RuleLoadError("rule files must remain inside the rule directory")
                file_bytes = path.stat().st_size
                if file_bytes > MAX_RULE_FILE_BYTES:
                    raise RuleLoadError("rule file exceeds the size limit")
                total_bytes += file_bytes
                if total_bytes > MAX_RULE_TOTAL_BYTES:
                    raise RuleLoadError("rule total byte count exceeds the limit")
                raw = json.loads(
                    path.read_text(encoding="utf-8"),
                    object_pairs_hook=_strict_json_object,
                )
                if not isinstance(raw, Mapping):
                    raise RuleLoadError("each rule file must contain one JSON object")
                rule = RcaRule.model_validate(raw)
                assert_model_safe(rule)
                loaded.append(rule)
            except RuleLoadError:
                raise
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                _DuplicateJsonKeyError,
                ValidationError,
                SensitiveDataError,
            ):
                raise RuleLoadError(f"rule file #{index + 1} is invalid") from None

        identities: set[tuple[str, str]] = set()
        current_versions: dict[str, str] = {}
        for rule in loaded:
            identity = (rule.rule_id, rule.version)
            if identity in identities:
                raise RuleLoadError(
                    f"duplicate rule identity {rule.rule_id!r} version {rule.version!r}"
                )
            identities.add(identity)
            if not rule.is_current:
                continue
            if rule.rule_id in current_versions:
                raise RuleLoadError(
                    f"multiple current versions for rule {rule.rule_id!r}"
                )
            current_versions[rule.rule_id] = rule.version

        return tuple(
            sorted(loaded, key=lambda rule: (rule.rule_id, rule.version))
        )

    def load_all(self) -> tuple[RcaRule, ...]:
        """Return only current rules for Detector callers.

        Historical files remain fully validated by ``load_all_versions`` and
        are available solely through exact version resolution for RCA.
        """

        return tuple(
            rule for rule in self.load_all_versions() if rule.is_current
        )

    def get_version(self, rule_id: str, version: str) -> RcaRule | None:
        """Return one exact current or historical version without fallback."""

        return next(
            (
                rule
                for rule in self.load_all_versions()
                if rule.rule_id == rule_id and rule.version == version
            ),
            None,
        )

    async def resolve_typed(self, incident: Incident) -> RuleResolution:
        """Resolve immutable Incident provenance against all rule versions."""

        return resolve_rules_for_incident(incident, self.load_all_versions())

    async def match_typed(self, incident: Incident) -> tuple[RcaRule, ...]:
        """Return only exactly version-bound rules applicable to the snapshot."""

        return (await self.resolve_typed(incident)).rules

    async def match(self, incident: Incident) -> tuple[Mapping[str, object], ...]:
        """P1 RuleRepository adapter returning strict JSON-safe mappings."""

        rules = await self.match_typed(incident)
        return tuple(rule.model_dump(mode="json") for rule in rules)


__all__ = [
    "BUBBLERAN_REPLAY_DETECTOR_ALGORITHM",
    "BUBBLERAN_REPLAY_RULE_ID",
    "FactPredicate",
    "JsonRuleRepository",
    "MAX_RULE_FILES",
    "MAX_RULE_TOTAL_BYTES",
    "PredicateGroup",
    "RULE_SCHEMA_VERSION",
    "RcaRule",
    "RuleResolution",
    "RuleResolutionIssue",
    "RuleResolutionIssueCode",
    "RuleResolutionStatus",
    "RuleAnalysis",
    "RuleDetection",
    "RuleLoadError",
    "RuleSeverity",
    "SeverityCase",
    "compare_values",
    "detection_matches",
    "resolve_rules_for_incident",
    "rule_content_sha256",
]
