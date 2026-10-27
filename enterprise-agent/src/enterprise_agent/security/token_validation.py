"""Microsoft Entra access-token signature and claims validation."""

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import jwt

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal

ALLOWED_ALGORITHMS = {"RS256"}
MAX_CACHED_TENANTS = 512


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
        self._cache: OrderedDict[str, _TenantKeys] = OrderedDict()

    async def validate(self, token: str) -> Principal:
        if not self.settings.entra_is_configured:
            raise AuthenticationError(
                "auth_not_configured",
                "Entra authentication is not configured",
            )
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except (jwt.PyJWTError, TypeError, ValueError, OverflowError) as exc:
            raise AuthenticationError("invalid_token", "The bearer token is malformed") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        tenant_id = unverified.get("tid")
        if not isinstance(algorithm, str) or algorithm not in ALLOWED_ALGORITHMS or not key_id:
            raise AuthenticationError("invalid_token", "The bearer token algorithm is not allowed")
        if not isinstance(key_id, str):
            raise AuthenticationError("invalid_token", "The token signing key is invalid")
        if not isinstance(tenant_id, str) or not self.settings.tenant_is_allowed(tenant_id):
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
        except (jwt.PyJWTError, TypeError, ValueError, OverflowError) as exc:
            raise AuthenticationError("invalid_token", "The bearer token is invalid") from exc

        if claims.get("ver") != "2.0":
            raise AuthenticationError("invalid_token", "Only Entra v2 access tokens are accepted")
        scope_claim = claims.get("scp", "")
        if not isinstance(scope_claim, str):
            raise AuthenticationError("invalid_token", "The bearer token is invalid")
        scopes = frozenset(scope_claim.split())
        if not self.settings.required_scopes <= scopes:
            raise AuthenticationError(
                "insufficient_scope",
                "The bearer token lacks a required scope",
            )
        object_id = claims.get("oid")
        if not isinstance(object_id, str) or not object_id:
            raise AuthenticationError("invalid_token", "A delegated user object ID is required")

        client_id = claims.get("azp") or claims.get("appid")
        if client_id is not None and not isinstance(client_id, str):
            raise AuthenticationError("invalid_token", "The bearer token is invalid")
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
            self._cache.move_to_end(tenant_id)
            return cached

        discovery_url = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
        )
        try:
            if self._http_client is None:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    issuer, jwks = await self._load_signing_metadata(
                        client, discovery_url, tenant_id
                    )
            else:
                issuer, jwks = await self._load_signing_metadata(
                    self._http_client, discovery_url, tenant_id
                )
            keys = {}
            for item in jwks["keys"]:
                if not isinstance(item, dict) or not isinstance(item.get("kid"), str):
                    raise ValueError("Invalid signing key")
                if item.get("kty") != "RSA" or item.get("use", "sig") != "sig":
                    continue
                if item.get("alg", "RS256") not in ALLOWED_ALGORITHMS:
                    continue
                keys[item["kid"]] = jwt.PyJWK.from_dict(item, algorithm="RS256")
            if not keys:
                raise ValueError("No accepted signing keys")
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
        self._cache.move_to_end(tenant_id)
        while len(self._cache) > MAX_CACHED_TENANTS:
            self._cache.popitem(last=False)
        return result

    @staticmethod
    async def _load_signing_metadata(
        client: httpx.AsyncClient, discovery_url: str, tenant_id: str
    ) -> tuple[str, dict]:
        metadata_response = await client.get(discovery_url, follow_redirects=False)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        if not isinstance(metadata, dict) or metadata.get("issuer") != issuer:
            raise ValueError("Unexpected issuer")
        jwks_uri = metadata.get("jwks_uri")
        if not isinstance(jwks_uri, str):
            raise ValueError("Invalid signing key URL")
        location = urlsplit(jwks_uri)
        if (
            location.scheme != "https"
            or location.hostname != "login.microsoftonline.com"
            or location.port not in (None, 443)
            or location.username is not None
            or location.password is not None
            or location.fragment
        ):
            raise ValueError("Untrusted signing key URL")
        jwks_response = await client.get(jwks_uri, follow_redirects=False)
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ValueError("Invalid signing key metadata")
        return issuer, jwks
