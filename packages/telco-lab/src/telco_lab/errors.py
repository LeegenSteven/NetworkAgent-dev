"""Stable, non-sensitive errors for the local data laboratory."""

from __future__ import annotations


_MESSAGES: dict[str, str] = {
    "invalid_arguments": "command arguments are invalid",
    "invalid_catalog": "the dataset catalog is invalid",
    "catalog_unavailable": "the dataset catalog is unavailable",
    "resource_not_found": "the requested catalog resource does not exist",
    "license_not_accepted": "the catalog license must be explicitly accepted",
    "unsafe_source": "the catalog source is not permitted",
    "unsafe_redirect": "the download redirect is not permitted",
    "unexpected_response": "the download server returned an unexpected response",
    "download_too_large": "the download exceeded its fixed byte limit",
    "size_mismatch": "the downloaded size does not match the catalog",
    "digest_mismatch": "the downloaded digest does not match the catalog",
    "download_failed": "the dataset download failed",
    "workspace_busy": "another workspace operation is in progress",
    "workspace_unsafe": "the workspace layout is not permitted",
    "lock_invalid": "the workspace lock is invalid",
    "artifact_unverified": "the requested artifact is not verified",
    "adapter_invalid_input": "the adapter input is invalid",
    "adapter_unsafe_field": "the adapter input contains a prohibited field",
    "adapter_limit_exceeded": "the adapter input exceeded a fixed limit",
    "internal_error": "the request could not be completed",
}


class LabError(Exception):
    """An error carrying a stable code and a deliberately generic message."""

    def __init__(self, code: str) -> None:
        if code not in _MESSAGES:
            code = "internal_error"
        self.code = code
        super().__init__(_MESSAGES[code])


__all__ = ["LabError"]
