"""Self-service erasure, exclusively scoped to the verified request principal."""

from fastapi import APIRouter, HTTPException, Request

from enterprise_agent.api.auth import PrincipalDependency

router = APIRouter(prefix="/api/me", tags=["data lifecycle"])


@router.delete("/data", status_code=204)
async def delete_my_data(request: Request, principal: PrincipalDependency):
    if not principal.authenticated:
        raise HTTPException(401, "Verified authentication is required")
    if request.query_params or await request.body():
        raise HTTPException(422, "This endpoint accepts no identity selectors or request body")
    try:
        await request.app.state.lifecycle.delete_user(principal)
    except OSError:
        raise HTTPException(503, "Local erasure is pending; retry the request") from None
