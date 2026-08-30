from __future__ import annotations

import pytest

from telco_lab.safe_json import StrictJsonError, load_strict_json


@pytest.mark.parametrize(
    "payload",
    [
        b'{"sha256":"first","sha256":"second"}',
        b'{"size_bytes":NaN}',
        b'{"size_bytes":Infinity}',
        b'{"size_bytes":-Infinity}',
        b'{"text":"\\ud800"}',
        b'{"text":"\\udc00"}',
        b"\xff\xfe{\x00}\x00",
    ],
)
def test_strict_json_rejects_ambiguous_or_invalid_payloads(payload: bytes) -> None:
    with pytest.raises(StrictJsonError):
        load_strict_json(payload, max_bytes=1024, max_depth=8)


def test_strict_json_enforces_byte_and_depth_budgets() -> None:
    with pytest.raises(StrictJsonError):
        load_strict_json(b'{"value":"too large"}', max_bytes=4, max_depth=8)
    with pytest.raises(StrictJsonError):
        load_strict_json(b"[[[[0]]]]", max_bytes=1024, max_depth=3)


def test_strict_json_accepts_unicode_and_nonfinite_words_inside_strings() -> None:
    parsed = load_strict_json(
        '{"label":"网络 NaN","emoji":"\\ud83d\\ude80"}',
        max_bytes=1024,
        max_depth=8,
    )
    assert parsed == {"label": "网络 NaN", "emoji": "🚀"}
