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


@pytest.mark.parametrize("kind", ["docx", "pptx", "xlsx", "pdf", "markdown", "json"])
@pytest.mark.parametrize("language,prefix", [("vi", "Mục"), ("en", "Section")])
def test_nonempty_section_missing_heading_gets_a_numbered_heading(kind, language, prefix):
    import copy

    payload = {
        "artifact_type": kind,
        "title": "Report",
        "language": language,
        "sections": [
            *[{"heading": f"Topic {index}", "body": "Verified content"} for index in range(1, 7)],
            {"body": "Verified final section preserved"},
        ],
    }
    original = copy.deepcopy(payload)
    spec = ArtifactSpec.model_validate(payload)
    assert spec.sections[6].heading == f"{prefix} 7"
    assert spec.sections[6].body == "Verified final section preserved"
    assert spec.sections[0].heading == "Topic 1"
    assert payload == original


@pytest.mark.parametrize("heading", [None, "", "   "])
def test_nonempty_bullets_can_get_a_heading_without_rewriting_content(heading):
    spec = ArtifactSpec.model_validate(
        {
            "artifact_type": "docx",
            "title": "Report",
            "sections": [{"heading": heading, "bullets": ["Verified finding"]}],
        }
    )
    assert spec.sections[0].heading == "Mục 1"
    assert spec.sections[0].bullets == ["Verified finding"]


@pytest.mark.parametrize("section", [{}, {"body": "  "}, {"body": 123}, {"bullets": []}])
def test_heading_repair_does_not_make_an_empty_or_invalid_section_valid(section):
    with pytest.raises(ValidationError):
        ArtifactSpec.model_validate(
            {"artifact_type": "docx", "title": "Report", "sections": [section]}
        )


def test_heading_repair_keeps_content_and_slide_limits():
    with pytest.raises(ValidationError, match="1200"):
        ArtifactSpec.model_validate(
            {
                "artifact_type": "pptx",
                "title": "Dense slide",
                "sections": [{"body": "x" * 1201}],
            }
        )
