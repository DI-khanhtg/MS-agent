import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from pypdf import PdfReader

from enterprise_agent.artifacts.models import ArtifactFormat, ArtifactSpec
from enterprise_agent.artifacts.renderers import (
    ArtifactRenderingError,
    ArtifactToolRenderer,
    DocxRenderer,
    JsonRenderer,
    MarkdownRenderer,
    PdfRenderer,
)
from enterprise_agent.artifacts.templates import CompanyTemplate
from enterprise_agent.config import Settings


@pytest.fixture
def artifact_spec() -> ArtifactSpec:
    return ArtifactSpec(
        artifact_type="markdown",
        title="Báo cáo Project X",
        subtitle="Cập nhật tuần",
        sections=[
            {
                "heading": "Tổng quan",
                "body": "Dự án đang đi đúng hướng.",
                "bullets": ["Hoàn tất thiết kế", "Xác nhận lịch kiểm thử"],
            }
        ],
        tables=[
            {
                "title": "Open actions",
                "columns": ["Action", "Owner"],
                "rows": [["Kiểm thử", "An"]],
            }
        ],
        sources=[{"label": "Weekly note", "reference": "workiq://files/project-x"}],
    )


@pytest.fixture
def company_template() -> CompanyTemplate:
    return CompanyTemplate(
        template_id="data_impact",
        company_name="Data Impact",
        primary_color="#164E63",
        accent_color="#0EA5E9",
        font_family="Calibri",
    )


def test_markdown_and_json_renderers_preserve_unicode_and_sources(
    artifact_spec: ArtifactSpec,
    company_template: CompanyTemplate,
) -> None:
    markdown = MarkdownRenderer().render(artifact_spec, company_template)
    payload = JsonRenderer().render(
        artifact_spec.model_copy(update={"artifact_type": ArtifactFormat.JSON}),
        company_template,
    )

    assert "Báo cáo Project X" in markdown.content.decode("utf-8")
    assert "workiq://files/project-x" in markdown.content.decode("utf-8")
    assert json.loads(payload.content)["resolved_template"]["company_name"] == "Data Impact"


def test_docx_and_pdf_renderers_create_valid_documents(
    tmp_path: Path,
    artifact_spec: ArtifactSpec,
    company_template: CompanyTemplate,
) -> None:
    docx = DocxRenderer().render(
        artifact_spec.model_copy(update={"artifact_type": ArtifactFormat.DOCX}),
        company_template,
    )
    pdf = PdfRenderer().render(
        artifact_spec.model_copy(update={"artifact_type": ArtifactFormat.PDF}),
        company_template,
    )
    docx_path = tmp_path / "report.docx"
    pdf_path = tmp_path / "report.pdf"
    docx_path.write_bytes(docx.content)
    pdf_path.write_bytes(pdf.content)

    assert Document(docx_path).paragraphs[1].text == "Báo cáo Project X"
    assert len(PdfReader(pdf_path).pages) >= 1


def test_artifact_tool_renderer_requires_configured_runtime(
    artifact_spec: ArtifactSpec,
    company_template: CompanyTemplate,
) -> None:
    renderer = ArtifactToolRenderer("node", None, 10)

    with pytest.raises(ArtifactRenderingError, match="ARTIFACT_TOOL_NODE_MODULES"):
        renderer.render(
            artifact_spec.model_copy(update={"artifact_type": ArtifactFormat.XLSX}),
            company_template,
        )


@pytest.mark.artifact_tool
@pytest.mark.parametrize("artifact_type", [ArtifactFormat.XLSX, ArtifactFormat.PPTX])
def test_artifact_tool_creates_valid_office_package(
    tmp_path: Path,
    artifact_spec: ArtifactSpec,
    company_template: CompanyTemplate,
    artifact_type: ArtifactFormat,
) -> None:
    settings = Settings(_env_file=None)
    if settings.artifact_tool_entrypoint is None:
        pytest.skip("bundled @oai/artifact-tool is unavailable")
    rendered = ArtifactToolRenderer(
        settings.artifact_node_executable,
        settings.artifact_tool_entrypoint,
        90,
    ).render(artifact_spec.model_copy(update={"artifact_type": artifact_type}), company_template)
    output = tmp_path / f"report.{artifact_type.value}"
    output.write_bytes(rendered.content)

    with ZipFile(output) as package:
        names = package.namelist()
    expected = "xl/workbook.xml" if artifact_type is ArtifactFormat.XLSX else "ppt/presentation.xml"
    assert expected in names

