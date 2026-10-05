"""Schemas for controlled actions and approval state."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CHAT_ID_PATTERN = re.compile(r"^[A-Za-z0-9:@._-]{1,300}$")


class ActionType(StrEnum):
    SEND_EMAIL = "send_email"
    CREATE_CALENDAR_EVENT = "create_calendar_event"
    POST_TEAMS_MESSAGE = "post_teams_message"
    UPDATE_ENTITY = "update_entity"


class ActionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


class EmailPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: list[str] = Field(min_length=1, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("to", "cc")
    @classmethod
    def validate_recipients(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().casefold() for value in values]
        if any(not EMAIL_PATTERN.fullmatch(value) for value in cleaned):
            raise ValueError("email recipients must be valid addresses")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("email recipients must be unique within each field")
        return cleaned

    @field_validator("subject", "body")
    @classmethod
    def strip_email_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("email subject and body must not be blank")
        return normalized


class CalendarEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    time_zone: str = Field(default="UTC", min_length=1, max_length=100)
    attendees: list[str] = Field(default_factory=list, max_length=50)
    body: str = Field(default="", max_length=20_000)
    location: str = Field(default="", max_length=500)

    @field_validator("subject", "time_zone")
    @classmethod
    def strip_required_calendar_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calendar subject and time_zone must not be blank")
        return normalized

    @field_validator("body", "location")
    @classmethod
    def strip_optional_calendar_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("attendees")
    @classmethod
    def validate_attendees(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().casefold() for value in values]
        if any(not EMAIL_PATTERN.fullmatch(value) for value in cleaned):
            raise ValueError("calendar attendees must be valid email addresses")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("calendar attendees must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_time_range(self) -> "CalendarEventPayload":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("calendar start and end must include a UTC offset")
        if self.end <= self.start:
            raise ValueError("calendar end must be after start")
        return self


class TeamsMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        normalized = value.strip()
        if not CHAT_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Teams chat_id contains unsupported characters")
        return normalized

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Teams message content must not be blank")
        return normalized


class UpdateEntityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_url: str = Field(min_length=1, max_length=1_000)
    changes: dict[str, Any] = Field(min_length=1, max_length=20)

    @field_validator("entity_url")
    @classmethod
    def strip_entity_url(cls, value: str) -> str:
        return value.strip()


ACTION_PAYLOAD_MODELS = {
    ActionType.SEND_EMAIL: EmailPayload,
    ActionType.CREATE_CALENDAR_EVENT: CalendarEventPayload,
    ActionType.POST_TEAMS_MESSAGE: TeamsMessagePayload,
    ActionType.UPDATE_ENTITY: UpdateEntityPayload,
}


class DraftActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    payload: dict[str, Any]


class RejectActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("rejection reason must not be blank")
        return normalized


class ActionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: ActionType
    status: ActionStatus
    summary: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    rejection_reason: str | None = None
    provider_status_code: int | None = None
    provider_resource_id: str | None = None
    verification: str | None = None
    error_code: str | None = None


def validate_action_payload(action_type: ActionType, payload: dict[str, Any]) -> dict[str, Any]:
    model = ACTION_PAYLOAD_MODELS[action_type]
    return model.model_validate(payload).model_dump(mode="json")


def summarize_action(action_type: ActionType, payload: dict[str, Any]) -> str:
    if action_type is ActionType.SEND_EMAIL:
        return f"Send email '{payload['subject']}' to {len(payload['to'])} recipient(s)"
    if action_type is ActionType.CREATE_CALENDAR_EVENT:
        return f"Create calendar event '{payload['subject']}' at {payload['start']}"
    if action_type is ActionType.POST_TEAMS_MESSAGE:
        return f"Post Teams message to chat {payload['chat_id']}"
    return f"Update approved entity {payload['entity_url']}"
