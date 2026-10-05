from pathlib import Path

import pytest

from enterprise_agent.artifacts.templates import TemplateCatalog


def test_template_catalog_resolves_default_brand(tmp_path: Path) -> None:
    config = tmp_path / "artifacts.yaml"
    root = tmp_path / "templates"
    root.mkdir()
    config.write_text(
        "default_template: company\n"
        "templates:\n"
        "  company:\n"
        "    company_name: Example Co\n"
        "    primary_color: '#123456'\n"
        "    accent_color: '#ABCDEF'\n"
        "    font_family: Calibri\n",
        encoding="utf-8",
    )

    catalog = TemplateCatalog.from_yaml(config, root)

    assert catalog.get("default").company_name == "Example Co"
    assert catalog.template_ids == ["company"]


def test_template_catalog_rejects_path_outside_allowlisted_root(tmp_path: Path) -> None:
    config = tmp_path / "artifacts.yaml"
    root = tmp_path / "templates"
    root.mkdir()
    (tmp_path / "outside.docx").write_bytes(b"not-a-template")
    config.write_text(
        "default_template: company\n"
        "templates:\n"
        "  company:\n"
        "    docx_template: ../outside.docx\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes"):
        TemplateCatalog.from_yaml(config, root)


def test_template_catalog_rejects_unknown_template(tmp_path: Path) -> None:
    config = tmp_path / "artifacts.yaml"
    root = tmp_path / "templates"
    root.mkdir()
    config.write_text(
        "default_template: company\ntemplates:\n  company:\n    company_name: Example Co\n",
        encoding="utf-8",
    )
    catalog = TemplateCatalog.from_yaml(config, root)

    with pytest.raises(ValueError, match="Unknown artifact template"):
        catalog.get("unknown")
