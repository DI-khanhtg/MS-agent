"""Bounded, authenticated folder imports for a local development instance."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.security.identity import (
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.local.documents import LocalDocumentError, LocalDocumentTools

router = APIRouter(prefix="/api/local-documents", tags=["local documents"])


@router.post("/upload")
async def upload_documents(
    request: Request,
    principal: PrincipalDependency,
    files: Annotated[list[UploadFile], File()],
) -> dict:
    settings = request.app.state.settings
    token = set_request_context(RequestContext(principal))
    try:
        if (
            not principal.authenticated
            or settings.app_env != "development"
            or "local_read" not in settings.tool_groups
        ):
            raise HTTPException(403, "Authenticated local document imports are disabled")
        if not files or len(files) > settings.local_max_files:
            raise HTTPException(422, "Import file count exceeds the configured limit")
        byte_limit = min(settings.local_max_file_bytes, 2_000_000)
        batch = []
        for file in files:
            content = await file.read(byte_limit + 1)
            if len(content) > byte_limit:
                raise HTTPException(413, "File exceeds the configured import size limit")
            batch.append((file.filename or "", content))
        local_tools: LocalDocumentTools = request.app.state.local_document_tools
        return await asyncio.to_thread(local_tools.import_documents, batch)
    except LocalDocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        reset_request_context(token)
        for file in files:
            await file.close()
