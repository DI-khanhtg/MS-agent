"""FastAPI authentication dependency."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from enterprise_agent.api.schemas import PrincipalResponse
from enterprise_agent.config import PLACEHOLDER_VALUES, Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.security.token_validation import AuthenticationError, TokenValidator

bearer_scheme = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api", tags=["authentication"])
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


@router.get("/auth/config")
async def browser_auth_configuration(request: Request) -> JSONResponse:
    """Public PKCE bootstrap; backend credentials and tokens never leave the server."""
    settings: Settings = request.app.state.settings
    result = {
        "deployment_mode": settings.deployment_mode,
        "enabled": settings.auth_enabled,
        "configured": False,
        "client_id": None,
        "authority": None,
        "redirect_uri": settings.entra_spa_redirect_uri,
        "scopes": [],
        "error": None,
    }
    if settings.auth_enabled:
        try:
            client_id = settings.entra_spa_client_id.strip()
            if client_id in PLACEHOLDER_VALUES:
                if len(settings.allowed_client_ids) != 1:
                    raise ValueError("Set ENTRA_SPA_CLIENT_ID to one allowed frontend client ID")
                client_id = next(iter(settings.allowed_client_ids))
            if client_id not in settings.allowed_client_ids:
                raise ValueError("ENTRA_SPA_CLIENT_ID must be in ENTRA_ALLOWED_CLIENT_IDS")
            client_id = str(UUID(client_id))
            backend_id = str(UUID(settings.azure_client_id))
            if backend_id not in settings.entra_api_audiences or not settings.required_scopes:
                raise ValueError("Configure the backend audience, allowed tenant and API scopes")
            if settings.auth_tenant_mode == "organizations":
                authority_tenant = "organizations"
            else:
                authority_tenant = str(UUID(settings.azure_tenant_id))
                if not settings.tenant_is_allowed(authority_tenant):
                    raise ValueError("Configure an allowed tenant")
            result.update(
                configured=True,
                client_id=client_id,
                authority=f"https://login.microsoftonline.com/{authority_tenant}",
                scopes=sorted(f"api://{backend_id}/{scope}" for scope in settings.required_scopes),
            )
        except ValueError:
            result["error"] = (
                "Sign-in is temporarily unavailable. Please contact the service administrator."
                if settings.deployment_mode == "shared"
                else (
                    "Check ENTRA_SPA_CLIENT_ID, ENTRA_ALLOWED_CLIENT_IDS, "
                    "AZURE_TENANT_ID, AZURE_CLIENT_ID and the backend API scopes"
                )
            )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def get_principal(request: Request, credentials: BearerCredentials) -> Principal:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        principal = Principal.anonymous()
        await _limit(request, principal)
        return principal
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
        principal = await validator.validate(credentials.credentials)
        await _limit(request, principal)
        return principal
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


async def _limit(request: Request, principal: Principal) -> None:
    key = (
        principal.isolation_key
        if principal.authenticated
        else (request.client.host if request.client else "anonymous")
    )
    if not await request.app.state.limiter.allow(key):
        raise HTTPException(
            429, "Request limit reached; retry in one minute", headers={"Retry-After": "60"}
        )


@router.get("/me", response_model=PrincipalResponse)
async def current_principal(principal: PrincipalDependency) -> PrincipalResponse:
    return PrincipalResponse(
        authenticated=principal.authenticated,
        tenant_id=principal.tenant_id,
        object_id=principal.object_id,
        username=principal.username,
        scopes=sorted(principal.scopes),
    )
