"""Validated, format-neutral artifact schemas."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ArtifactFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    PDF = "pdf"


class ArtifactSection(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=8_000)
    bullets: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("heading", "body")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("bullets")
    @classmethod
    def validate_bullets(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 1_000 for value in cleaned):
            raise ValueError("bullets must be non-blank and at most 1000 characters")
        return cleaned


class ArtifactTable(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    columns: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[str | int | float | bool | None]] = Field(default_factory=list, max_length=500)

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("table columns must be non-blank and at most 100 characters")
        if len({value.casefold() for value in cleaned}) != len(cleaned):
            raise ValueError("table columns must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_row_width(self) -> "ArtifactTable":
        expected = len(self.columns)
        for index, row in enumerate(self.rows, start=1):
            if len(row) != expected:
                raise ValueError(f"table row {index} has {len(row)} cells; expected {expected}")
            if any(isinstance(value, str) and len(value) > 2_000 for value in row):
                raise ValueError(f"table row {index} contains a cell longer than 2000 characters")
        return self


class ArtifactSource(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    reference: str = Field(min_length=1, max_length=2_000)

    @field_validator("label", "reference")
    @classmethod
    def strip_source(cls, value: str) -> str:
        return value.strip()


class ArtifactSpec(BaseModel):
    artifact_type: ArtifactFormat
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, max_length=500)
    template_id: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    language: str = Field(default="vi", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    sections: list[ArtifactSection] = Field(default_factory=list, max_length=50)
    tables: list[ArtifactTable] = Field(default_factory=list, max_length=20)
    sources: list[ArtifactSource] = Field(default_factory=list, max_length=50)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("title", "subtitle")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def require_content(self) -> "ArtifactSpec":
        if not self.sections and not self.tables:
            raise ValueError("at least one section or table is required")
        if len(self.metadata) > 50:
            raise ValueError("metadata supports at most 50 entries")
        if any(len(str(key)) > 100 or len(str(value)) > 1_000 for key, value in self.metadata.items()):
            raise ValueError("metadata keys or values exceed the supported size")
        if self.artifact_type is ArtifactFormat.PPTX:
            for section in self.sections:
                text_size = len(section.body) + sum(len(item) for item in section.bullets)
                if text_size > 1_200:
                    raise ValueError(
                        "PPTX sections support at most 1200 characters; split dense content "
                        "into multiple sections"
                    )
            for table in self.tables:
                if len(table.columns) > 6:
                    raise ValueError("PPTX tables support at most 6 columns")
                if any(
                    isinstance(value, str) and len(value) > 240
                    for row in table.rows
                    for value in row
                ):
                    raise ValueError("PPTX table cells support at most 240 characters")
        return self


class ArtifactRecord(BaseModel):
    artifact_id: str
    title: str
    artifact_type: ArtifactFormat
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    download_url: str


class StoredArtifact(BaseModel):
    record: ArtifactRecord
    tenant_key: str
    user_key: str
    content_sha256: str


def schema_for_agent() -> dict[str, Any]:
    """Expose the canonical nested JSON schema for diagnostics and tests."""
    return ArtifactSpec.model_json_schema()
