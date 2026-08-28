from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from a2a.types import DataPart, FilePart, FileWithUri, Message, Part, Role, TextPart
from pydantic import ValidationError

from telco_assurance_agent.protocol import (
    AssuranceAnalysisRequest,
    AssuranceConfirmationRequest,
    AssuranceProtocolError,
    AssuranceScanRequest,
    parse_request_message,
)


def _common(message_type: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "message_type": message_type,
        "message_id": uuid4().hex,
        "workflow_id": uuid4().hex,
        "trace_id": uuid4().hex,
        "idempotency_key": uuid4().hex,
        "sent_at": datetime(2030, 1, 1, tzinfo=UTC).isoformat(),
    }


def _message(data: dict[str, object], *extra_parts: Part) -> Message:
    return Message(
        role=Role.user,
        message_id=str(data["message_id"]),
        parts=[Part(root=DataPart(data=data)), *extra_parts],
    )


def _scan_data() -> dict[str, object]:
    return {
        **_common("assurance_scan_request"),
        "window_start": "2025-11-20T00:00:00Z",
        "window_end": "2025-11-29T23:59:59Z",
        "resource_ids": ["lte:enodeb:1:cell:00123"],
        "page_size": 20,
        "page_offset": 0,
    }


def test_parse_scan_request_normalizes_utc_and_lte_resources() -> None:
    data = _scan_data()
    parsed = parse_request_message(
        _message(data, Part(root=TextPart(text="请扫描本地 LTE 数据。")))
    )

    assert isinstance(parsed, AssuranceScanRequest)
    assert parsed.window_start == datetime(2025, 11, 20, tzinfo=UTC)
    assert parsed.resource_ids == ("lte:enodeb:1:cell:123",)


def test_scan_request_allows_only_a_null_pair_for_approved_full_asset() -> None:
    data = _scan_data()
    data.update(window_start=None, window_end=None)
    assert isinstance(parse_request_message(_message(data)), AssuranceScanRequest)

    data["window_end"] = "2025-11-29T23:59:59Z"
    with pytest.raises(AssuranceProtocolError, match="invalid canonical request"):
        parse_request_message(_message(data))


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (
            datetime(2025, 11, 20),
            datetime(2025, 11, 21, tzinfo=UTC),
        ),
        (
            datetime(2025, 11, 20, tzinfo=timezone(timedelta(hours=8))),
            datetime(2025, 11, 21, tzinfo=timezone(timedelta(hours=8))),
        ),
        (
            datetime(2025, 11, 1, tzinfo=UTC),
            datetime(2025, 12, 3, tzinfo=UTC),
        ),
    ),
)
def test_scan_request_rejects_non_utc_or_oversized_windows(
    start: datetime,
    end: datetime,
) -> None:
    data = _scan_data()
    data.update(window_start=start, window_end=end)
    with pytest.raises(AssuranceProtocolError, match="invalid canonical request"):
        parse_request_message(_message(data))


def test_scan_request_rejects_resource_and_page_capacity_violations() -> None:
    data = _scan_data()
    data["resource_ids"] = [f"lte:enodeb:{index}" for index in range(101)]
    with pytest.raises(AssuranceProtocolError):
        parse_request_message(_message(data))

    data = _scan_data()
    data["page_size"] = 21
    with pytest.raises(AssuranceProtocolError):
        parse_request_message(_message(data))


def test_parser_requires_one_data_part_and_matching_outer_message_id() -> None:
    data = _scan_data()
    mismatched = _message(data)
    mismatched.message_id = uuid4().hex
    with pytest.raises(AssuranceProtocolError, match="message identifier mismatch"):
        parse_request_message(mismatched)

    with pytest.raises(AssuranceProtocolError, match="exactly one DataPart"):
        parse_request_message(
            _message(data, Part(root=DataPart(data=dict(data))))
        )
    text_only = Message(
        role=Role.user,
        message_id=uuid4().hex,
        parts=[Part(root=TextPart(text="确认"))],
    )
    with pytest.raises(AssuranceProtocolError, match="exactly one DataPart"):
        parse_request_message(text_only)


def test_transport_and_business_identifiers_cannot_alias() -> None:
    data = _scan_data()
    message = _message(data)
    message.context_id = str(data["workflow_id"])
    with pytest.raises(AssuranceProtocolError, match="identifiers must be independent"):
        parse_request_message(message)

    confirmation = {
        **_common("assurance_confirmation_request"),
        "preview_message_id": uuid4().hex,
        "candidate_id": "incident-safe-candidate",
        "challenge_id": "c" * 43,
        "snapshot_sha256": "a" * 64,
        "decision": "CONFIRM",
        "reason": "用户明确确认创建 Incident",
    }
    confirmation["candidate_id"] = confirmation["trace_id"]
    with pytest.raises(AssuranceProtocolError, match="identifiers must be independent"):
        parse_request_message(_message(confirmation))


def test_parser_rejects_files_duplicate_text_and_oversized_text() -> None:
    data = _scan_data()
    file_part = Part(
        root=FilePart(file=FileWithUri(uri="https://example.invalid/file"))
    )
    with pytest.raises(AssuranceProtocolError, match="FilePart"):
        parse_request_message(_message(data, file_part))
    with pytest.raises(AssuranceProtocolError, match="at most one TextPart"):
        parse_request_message(
            _message(
                data,
                Part(root=TextPart(text="一")),
                Part(root=TextPart(text="二")),
            )
        )
    with pytest.raises(AssuranceProtocolError, match="4096"):
        parse_request_message(
            _message(data, Part(root=TextPart(text="中" * 4097)))
        )


def test_confirmation_and_analysis_requests_are_strictly_discriminated() -> None:
    confirmation = {
        **_common("assurance_confirmation_request"),
        "preview_message_id": uuid4().hex,
        "candidate_id": "incident-safe-candidate",
        "challenge_id": "c" * 43,
        "snapshot_sha256": "a" * 64,
        "decision": "CONFIRM",
        "reason": "用户明确确认创建 Incident",
    }
    assert isinstance(
        parse_request_message(_message(confirmation)),
        AssuranceConfirmationRequest,
    )
    confirmation["decision"] = "APPROVE"
    with pytest.raises(AssuranceProtocolError):
        parse_request_message(_message(confirmation))

    analysis = {
        **_common("assurance_analyze_request"),
        "incident_id": "incident-safe-candidate",
        "requested_report_version": 1,
    }
    assert isinstance(
        parse_request_message(_message(analysis)), AssuranceAnalysisRequest
    )
    analysis["unexpected"] = True
    with pytest.raises(AssuranceProtocolError):
        parse_request_message(_message(analysis))


def test_sensitive_values_are_rejected_without_echo() -> None:
    data = _scan_data()
    sensitive = "IMSI:310410000000001"
    data["workflow_id"] = sensitive
    data["message_id"] = uuid4().hex
    message = _message(data)
    with pytest.raises(AssuranceProtocolError) as error:
        parse_request_message(message)
    assert sensitive not in str(error.value)
    assert "310410000000001" not in str(error.value)


def test_direct_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AssuranceScanRequest.model_validate({**_scan_data(), "unknown": 1})
