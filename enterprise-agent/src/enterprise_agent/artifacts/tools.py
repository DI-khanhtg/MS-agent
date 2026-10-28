"""Agent Framework tool for validated local artifact generation."""

from enterprise_agent.artifacts.models import ArtifactSpec
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.recording import record_completed_artifact, record_tool_call


class ArtifactTools:
    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    async def create_artifact(self, spec: ArtifactSpec) -> dict[str, object]:
        """Create one downloadable artifact from a complete structured specification.

        The artifact_type must be markdown, json, docx, xlsx, pptx, or pdf. Use sections for
        narrative content, tables for rectangular evidence, sources for provenance, and an
        allowlisted template_id. This writes only to tenant-isolated local artifact storage;
        it never updates enterprise source systems.

        Match artifact_type to the requested OUTPUT: Word=docx, PowerPoint/slides=pptx,
        Excel=xlsx. Never substitute markdown for an Office file. Use template_id="default"
        unless a different configured template was requested. For example:
        {"spec":{"artifact_type":"docx","title":"Report","sections":[
          {"heading":"Summary","body":"Verified findings"}]}}
        For PPTX, keep each section below 1200 characters and tables at most 6 columns.
        The successful result contains download_url; return that exact download link.
        """
        # Agent Framework can supply nested tool arguments as dictionaries even when
        # their JSON schema comes from a Pydantic annotation. Validate before use/write.
        spec = ArtifactSpec.model_validate(spec)
        principal = get_request_context().principal
        # Record before the side effect: even a failed renderer must not replay the turn.
        record_tool_call(
            "create_artifact",
            {
                "artifact_type": spec.artifact_type.value,
                "template_id": spec.template_id,
            },
        )
        record = await self.service.create(spec, principal)
        receipt = record.model_dump(mode="json")
        record_completed_artifact(receipt)
        return receipt
