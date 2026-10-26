"""High-level artifact generation service."""

import asyncio

from enterprise_agent.artifacts.models import ArtifactRecord, ArtifactSpec
from enterprise_agent.artifacts.renderers import RendererRegistry
from enterprise_agent.artifacts.store import ArtifactStore
from enterprise_agent.artifacts.templates import TemplateCatalog
from enterprise_agent.security.identity import Principal
from enterprise_agent.telemetry.operations import audit_event, identity_hash


class ArtifactService:
    def __init__(
        self,
        store: ArtifactStore,
        templates: TemplateCatalog,
        renderers: RendererRegistry,
    ) -> None:
        self.store = store
        self.templates = templates
        self.renderers = renderers

    async def create(self, spec: ArtifactSpec, principal: Principal) -> ArtifactRecord:
        record = await asyncio.to_thread(self._create_sync, spec, principal)
        audit_event("artifact_created", artifact_id=record.artifact_id, status="ok",
                    subject_hash=identity_hash(principal.isolation_key))
        return record

    def _create_sync(self, spec: ArtifactSpec, principal: Principal) -> ArtifactRecord:
        template = self.templates.get(spec.template_id)
        rendered = self.renderers.render(spec, template)
        return self.store.save(
            principal=principal,
            title=spec.title,
            artifact_type=spec.artifact_type,
            extension=rendered.extension,
            media_type=rendered.media_type,
            content=rendered.content,
        )
