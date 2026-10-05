import pytest
from pydantic import ValidationError

from enterprise_agent.artifacts.models import ArtifactSpec, schema_for_agent


def test_artifact_spec_requires_content() -> None:
    with pytest.raises(ValidationError, match="at least one section or table"):
        ArtifactSpec(artifact_type="docx", title="Empty")


def test_artifact_spec_rejects_mismatched_table_rows() -> None:
    with pytest.raises(ValidationError, match="expected 2"):
        ArtifactSpec(
            artifact_type="xlsx",
            title="Bad table",
            tables=[{"columns": ["Owner", "Status"], "rows": [["An"]]}],
        )


def test_agent_schema_exposes_all_supported_formats() -> None:
    schema = schema_for_agent()
    artifact_format = schema["$defs"]["ArtifactFormat"]

    assert artifact_format["enum"] == ["markdown", "json", "docx", "xlsx", "pptx", "pdf"]


def test_pptx_rejects_table_too_wide_for_a_slide() -> None:
    with pytest.raises(ValidationError, match="at most 6 columns"):
        ArtifactSpec(
            artifact_type="pptx",
            title="Too wide",
            tables=[{"columns": [f"Column {index}" for index in range(7)], "rows": []}],
        )
