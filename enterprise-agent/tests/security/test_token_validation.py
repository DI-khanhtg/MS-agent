import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from enterprise_agent.config import Settings
from enterprise_agent.security.token_validation import AuthenticationError, EntraTokenValidator

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "22222222-2222-2222-2222-222222222222"
API_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
FRONTEND_CLIENT_ID = "44444444-4444-4444-4444-444444444444"
KEY_ID = "test-key-id"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "auth_enabled": True,
        "azure_tenant_id": TENANT_ID,
        "azure_client_id": API_CLIENT_ID,
        "azure_client_secret": "test-client-secret",
        "entra_api_audience": API_CLIENT_ID,
        "auth_allowed_tenant_ids": TENANT_ID,
        "entra_required_scopes": "access_as_user",
        "entra_allowed_client_ids": FRONTEND_CLIENT_ID,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def create_token(
    private_key: rsa.RSAPrivateKey,
    **claim_overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "aud": API_CLIENT_ID,
        "iss": ISSUER,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 600,
        "sub": "subject-a",
        "oid": "user-a",
        "tid": TENANT_ID,
        "scp": "access_as_user profile",
        "azp": FRONTEND_CLIENT_ID,
        "preferred_username": "user.a@example.com",
        "ver": "2.0",
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KEY_ID})


def create_validator(
    private_key: rsa.RSAPrivateKey,
    app_settings: Settings | None = None,
) -> tuple[EntraTokenValidator, list[str]]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={"issuer": ISSUER, "jwks_uri": "https://login.example.test/keys"},
            )
        return httpx.Response(200, json={"keys": [jwk]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EntraTokenValidator(app_settings or settings(), http_client=client), requests


@pytest.mark.asyncio
async def test_valid_delegated_token_builds_principal(private_key: rsa.RSAPrivateKey) -> None:
    validator, requests = create_validator(private_key)

    principal = await validator.validate(create_token(private_key))

    assert principal.tenant_id == TENANT_ID
    assert principal.object_id == "user-a"
    assert principal.client_id == FRONTEND_CLIENT_ID
    assert principal.scopes == frozenset({"access_as_user", "profile"})
    assert principal.access_token
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_signing_keys_are_cached(private_key: rsa.RSAPrivateKey) -> None:
    validator, requests = create_validator(private_key)
    token = create_token(private_key)

    await validator.validate(token)
    await validator.validate(token)

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_cross_tenant_token_is_rejected_before_discovery(
    private_key: rsa.RSAPrivateKey,
) -> None:
    validator, requests = create_validator(private_key)
    token = create_token(
        private_key,
        tid=OTHER_TENANT_ID,
        iss=f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0",
    )

    with pytest.raises(AuthenticationError) as error:
        await validator.validate(token)

    assert error.value.code == "tenant_not_allowed"
    assert requests == []


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    validator, _ = create_validator(private_key)

    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, aud="another-api"))

    assert error.value.code == "invalid_token"


@pytest.mark.asyncio
async def test_application_only_token_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    validator, _ = create_validator(private_key)

    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, scp="", roles=["Application.Read.All"]))

    assert error.value.code == "insufficient_scope"


@pytest.mark.asyncio
async def test_unapproved_calling_client_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    validator, _ = create_validator(private_key)

    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, azp="unapproved-client"))

    assert error.value.code == "client_not_allowed"

