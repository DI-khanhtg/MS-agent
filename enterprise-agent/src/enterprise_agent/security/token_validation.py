"""Microsoft Entra access-token signature and claims validation."""

import time
from dataclasses import dataclass
from typing import Protocol

import httpx
import jwt

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal

ALLOWED_ALGORITHMS = {"RS256"}


class AuthenticationError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class TokenValidator(Protocol):
    async def validate(self, token: str) -> Principal: ...


@dataclass(slots=True)
class _TenantKeys:
    issuer: str
    keys: dict[str, jwt.PyJWK]
    expires_at: float


class EntraTokenValidator:
    """Validate v2 Entra JWTs against tenant-specific discovery metadata and JWKS."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self.settings = settings
        self._http_client = http_client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, _TenantKeys] = {}

    async def validate(self, token: str) -> Principal:
        if not self.settings.entra_api_audiences or not self.settings.allowed_tenant_ids:
            raise AuthenticationError(
                "auth_not_configured",
                "Entra authentication is not configured",
            )
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid_token", "The bearer token is malformed") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        tenant_id = unverified.get("tid")
        if algorithm not in ALLOWED_ALGORITHMS or not isinstance(key_id, str):
            raise AuthenticationError("invalid_token", "The bearer token algorithm is not allowed")
        if not isinstance(tenant_id, str) or tenant_id not in self.settings.allowed_tenant_ids:
            raise AuthenticationError("tenant_not_allowed", "The token tenant is not allowed")

        tenant_keys = await self._get_tenant_keys(tenant_id)
        key = tenant_keys.keys.get(key_id)
        if key is None:
            tenant_keys = await self._get_tenant_keys(tenant_id, force_refresh=True)
            key = tenant_keys.keys.get(key_id)
        if key is None:
            raise AuthenticationError("invalid_token", "The token signing key is unknown")

        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=list(self.settings.entra_api_audiences),
                issuer=tenant_keys.issuer,
                leeway=60,
                options={"require": ["aud", "exp", "iat", "iss", "sub", "tid"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token_expired", "The bearer token has expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid_token", "The bearer token is invalid") from exc

        if claims.get("ver") != "2.0":
            raise AuthenticationError("invalid_token", "Only Entra v2 access tokens are accepted")
        scopes = frozenset(str(claims.get("scp", "")).split())
        if not self.settings.required_scopes <= scopes:
            raise AuthenticationError(
                "insufficient_scope",
                "The bearer token lacks a required scope",
            )
        object_id = claims.get("oid")
        if not isinstance(object_id, str) or not object_id:
            raise AuthenticationError("invalid_token", "A delegated user object ID is required")

        client_id = claims.get("azp") or claims.get("appid")
        if self.settings.allowed_client_ids and client_id not in self.settings.allowed_client_ids:
            raise AuthenticationError("client_not_allowed", "The calling client is not allowed")

        return Principal(
            tenant_id=tenant_id,
            object_id=object_id,
            subject=str(claims["sub"]),
            username=claims.get("preferred_username"),
            scopes=scopes,
            client_id=str(client_id) if client_id else None,
            access_token=token,
        )

    async def _get_tenant_keys(self, tenant_id: str, *, force_refresh: bool = False) -> _TenantKeys:
        cached = self._cache.get(tenant_id)
        if cached and not force_refresh and cached.expires_at > time.monotonic():
            return cached

        discovery_url = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/"
            ".well-known/openid-configuration"
        )
        try:
            if self._http_client is None:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    metadata_response = await client.get(discovery_url)
                    metadata_response.raise_for_status()
                    metadata = metadata_response.json()
                    jwks_response = await client.get(metadata["jwks_uri"])
                    jwks_response.raise_for_status()
                    jwks = jwks_response.json()
            else:
                metadata_response = await self._http_client.get(discovery_url)
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                jwks_response = await self._http_client.get(metadata["jwks_uri"])
                jwks_response.raise_for_status()
                jwks = jwks_response.json()
            issuer = metadata["issuer"]
            keys = {item["kid"]: jwt.PyJWK.from_dict(item) for item in jwks["keys"]}
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError(
                "identity_provider_unavailable",
                "Entra signing metadata could not be loaded",
            ) from exc
        result = _TenantKeys(
            issuer=issuer,
            keys=keys,
            expires_at=time.monotonic() + self._cache_ttl_seconds,
        )
        self._cache[tenant_id] = result
        return result
