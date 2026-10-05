"""Public API schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]


class ChatResponse(BaseModel):
    session_id: str
    message: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "enterprise-agent"
    environment: str
    model: str
    deepseek_configured: bool
    auth_enabled: bool
    entra_configured: bool
    workiq_obo_configured: bool
    fabric_obo_configured: bool
    artifact_tool_configured: bool
    write_approval_required: bool
    enabled_tool_groups: list[str]


class PrincipalResponse(BaseModel):
    authenticated: bool
    tenant_id: str
    object_id: str
    username: str | None
    scopes: list[str]


class ErrorResponse(BaseModel):
    error: str
    detail: str
