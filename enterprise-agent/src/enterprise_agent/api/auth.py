"""FastAPI authentication dependency."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from enterprise_agent.api.schemas import PrincipalResponse
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.security.token_validation import AuthenticationError, TokenValidator

bearer_scheme = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api", tags=["authentication"])
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_principal(request: Request, credentials: BearerCredentials) -> Principal:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return Principal.anonymous()
    if not settings.entra_is_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Entra authentication is enabled but not fully configured.",
        )
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="A bearer access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    validator: TokenValidator = request.app.state.token_validator
    try:
        return await validator.validate(credentials.credentials)
    except AuthenticationError as exc:
        http_status = (
            status.HTTP_403_FORBIDDEN
            if exc.code in {"tenant_not_allowed", "client_not_allowed", "insufficient_scope"}
            else status.HTTP_401_UNAUTHORIZED
        )
        raise HTTPException(
            http_status,
            detail=exc.detail,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.get("/me", response_model=PrincipalResponse)
async def current_principal(principal: PrincipalDependency) -> PrincipalResponse:
    return PrincipalResponse(
        authenticated=principal.authenticated,
        tenant_id=principal.tenant_id,
        object_id=principal.object_id,
        username=principal.username,
        scopes=sorted(principal.scopes),
    )
