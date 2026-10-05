"""Authenticated artifact creation, listing, and download endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.artifacts.models import ArtifactRecord, ArtifactSpec
from enterprise_agent.artifacts.renderers import ArtifactRenderingError
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.artifacts.store import ArtifactNotFoundError

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def get_artifact_service(request: Request) -> ArtifactService:
    return request.app.state.artifact_service


ArtifactServiceDependency = Annotated[ArtifactService, Depends(get_artifact_service)]


@router.post("", response_model=ArtifactRecord, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    spec: ArtifactSpec,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
) -> ArtifactRecord:
    try:
        return await service.create(spec, principal)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ArtifactRenderingError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("", response_model=list[ArtifactRecord])
async def list_artifacts(
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
) -> list[ArtifactRecord]:
    return service.store.list(principal)


@router.get("/{artifact_id}/download", response_class=FileResponse)
async def download_artifact(
    artifact_id: str,
    service: ArtifactServiceDependency,
    principal: PrincipalDependency,
) -> FileResponse:
    try:
        record, path = service.store.get(artifact_id, principal)
    except (ArtifactNotFoundError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Artifact not found") from None
    return FileResponse(path, media_type=record.media_type, filename=record.filename)

