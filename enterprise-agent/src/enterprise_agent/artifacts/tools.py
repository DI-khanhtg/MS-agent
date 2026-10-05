"""Agent Framework tool for validated local artifact generation."""

from enterprise_agent.artifacts.models import ArtifactSpec
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.security.identity import get_request_context


class ArtifactTools:
    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    async def create_artifact(self, spec: ArtifactSpec) -> dict[str, object]:
        """Create one downloadable artifact from a complete structured specification.

        The artifact_type must be markdown, json, docx, xlsx, pptx, or pdf. Use sections for
        narrative content, tables for rectangular evidence, sources for provenance, and an
        allowlisted template_id. This writes only to tenant-isolated local artifact storage;
        it never updates enterprise source systems.
        """
        principal = get_request_context().principal
        record = await self.service.create(spec, principal)
        return record.model_dump(mode="json")

