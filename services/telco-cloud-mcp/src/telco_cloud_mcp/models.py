"""Fixed response envelope and safe public errors."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CloudMcpInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str


class ToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "1.0"
    ok: bool
    data: Any | None = None
    error: ToolError | None = None


__all__ = ["CloudMcpInputError", "ToolError", "ToolResponse"]
