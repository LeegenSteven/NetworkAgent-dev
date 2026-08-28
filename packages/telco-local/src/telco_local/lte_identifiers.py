"""Canonical LTE resource identifiers for the deterministic Local Profile.

ETSI 3GPP TS 23.003 section 19.6 defines the E-UTRAN Cell Identity as a
28-bit value.  P2a deliberately applies that conservative bound to both local
eNodeB and Cell components so subscriber-sized decimal identifiers cannot be
smuggled through resource fields.
"""

from __future__ import annotations

import re
from typing import Literal


LTE_IDENTIFIER_MAX = 268_435_455
LTE_IDENTIFIER_DECIMAL_PATTERN = r"[0-9]{1,9}"
_DECIMAL_COMPONENT = re.compile(LTE_IDENTIFIER_DECIMAL_PATTERN)

LteComponent = Literal["eNodeB", "Cell"]


def normalize_lte_identifier(
    value: object,
    *,
    component: LteComponent,
) -> str:
    """Validate one 28-bit decimal component and remove leading zeroes.

    Errors intentionally identify only the field class, never the rejected
    value, because that value may itself be a disguised subscriber identifier.
    """

    raw = str(value).strip()
    if _DECIMAL_COMPONENT.fullmatch(raw) is None:
        raise ValueError(f"invalid LTE {component} identifier")
    parsed = int(raw, 10)
    if parsed > LTE_IDENTIFIER_MAX:
        raise ValueError(f"invalid LTE {component} identifier")
    return str(parsed)


def canonical_lte_resource_id(
    enodeb_id: object,
    cell_id: object | None = None,
) -> str:
    """Build a canonical Local Profile resource identity."""

    enodeb = normalize_lte_identifier(enodeb_id, component="eNodeB")
    if cell_id is None:
        return f"lte:enodeb:{enodeb}"
    cell = normalize_lte_identifier(cell_id, component="Cell")
    return f"lte:enodeb:{enodeb}:cell:{cell}"


def parse_lte_resource_id(value: object) -> tuple[str, str | None]:
    """Parse and normalize an eNodeB or Cell canonical resource selector."""

    raw = str(value).strip()
    segments = raw.split(":")
    if len(segments) == 3 and segments[:2] == ["lte", "enodeb"]:
        return (
            normalize_lte_identifier(segments[2], component="eNodeB"),
            None,
        )
    if (
        len(segments) == 5
        and segments[:2] == ["lte", "enodeb"]
        and segments[3] == "cell"
    ):
        return (
            normalize_lte_identifier(segments[2], component="eNodeB"),
            normalize_lte_identifier(segments[4], component="Cell"),
        )
    raise ValueError("invalid canonical LTE resource identifier")


__all__ = [
    "LTE_IDENTIFIER_DECIMAL_PATTERN",
    "LTE_IDENTIFIER_MAX",
    "canonical_lte_resource_id",
    "normalize_lte_identifier",
    "parse_lte_resource_id",
]
