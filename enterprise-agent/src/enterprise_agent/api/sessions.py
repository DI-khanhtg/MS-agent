"""Session history and deletion are isolated by verified identity."""

from fastapi import APIRouter, HTTPException, Request

from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.security.identity import RequestContext

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(request: Request, principal: PrincipalDependency):
    return await request.app.state.conversations.list(principal)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request, principal: PrincipalDependency):
    saved = await request.app.state.conversations.get(session_id, principal)
    if saved is None:
        raise HTTPException(404, "Session not found")
    return saved


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request, principal: PrincipalDependency):
    if not await request.app.state.conversations.delete(session_id, principal):
        raise HTTPException(404, "Session not found")
    runtime = request.app.state.agent_runtime
    if getattr(runtime, "_sessions", None):
        await runtime._sessions.delete(RequestContext(principal).session_storage_key(session_id))
