"""Allowlisted company artifact templates loaded from YAML."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class CompanyTemplate:
    template_id: str
    company_name: str
    primary_color: str
    accent_color: str
    font_family: str
    docx_template: Path | None = None

    def to_public_dict(self) -> dict[str, str]:
        return {
            "template_id": self.template_id,
            "company_name": self.company_name,
            "primary_color": self.primary_color,
            "accent_color": self.accent_color,
            "font_family": self.font_family,
        }


class TemplateCatalog:
    def __init__(self, templates: dict[str, CompanyTemplate], default_template: str) -> None:
        if default_template not in templates:
            raise ValueError("default artifact template is not defined")
        self._templates = templates
        self.default_template = default_template

    @classmethod
    def from_yaml(cls, config_path: Path, template_root: Path) -> "TemplateCatalog":
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        raw_templates = payload.get("templates", {})
        templates: dict[str, CompanyTemplate] = {}
        resolved_root = template_root.resolve()
        for template_id, raw in raw_templates.items():
            docx_template = _safe_optional_path(raw.get("docx_template"), resolved_root)
            templates[template_id] = CompanyTemplate(
                template_id=template_id,
                company_name=str(raw.get("company_name", "Enterprise")),
                primary_color=_validate_color(raw.get("primary_color", "#164E63")),
                accent_color=_validate_color(raw.get("accent_color", "#0EA5E9")),
                font_family=str(raw.get("font_family", "Calibri")),
                docx_template=docx_template,
            )
        return cls(templates, str(payload.get("default_template", "default")))

    def get(self, template_id: str) -> CompanyTemplate:
        resolved_id = self.default_template if template_id == "default" else template_id
        try:
            return self._templates[resolved_id]
        except KeyError as exc:
            raise ValueError(f"Unknown artifact template: {template_id}") from exc

    @property
    def template_ids(self) -> list[str]:
        return sorted(self._templates)


def _safe_optional_path(value: object, root: Path) -> Path | None:
    if not value:
        return None
    candidate = (root / str(value)).resolve()
    if root not in candidate.parents:
        raise ValueError("artifact template path escapes the configured template root")
    if not candidate.is_file():
        raise ValueError(f"artifact template file does not exist: {candidate.name}")
    return candidate


def _validate_color(value: object) -> str:
    color = str(value).upper()
    if len(color) != 7 or not color.startswith("#"):
        raise ValueError(f"Invalid artifact template color: {color}")
    try:
        int(color[1:], 16)
    except ValueError as exc:
        raise ValueError(f"Invalid artifact template color: {color}") from exc
    return color

