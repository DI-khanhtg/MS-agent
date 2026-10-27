import time
from typing import Any
from uuid import UUID

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
                json={"issuer": ISSUER, "jwks_uri": "https://login.microsoftonline.com/keys"},
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
async def test_token_issuer_must_match_verified_tenant(private_key: rsa.RSAPrivateKey) -> None:
    validator, _ = create_validator(private_key)
    with pytest.raises(AuthenticationError) as error:
        await validator.validate(
            create_token(
                private_key, iss=f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0"
            )
        )
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


@pytest.mark.asyncio
async def test_organizations_accepts_independently_signed_tenants() -> None:
    tenant_keys = {
        tenant: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for tenant in (TENANT_ID, OTHER_TENANT_ID)
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        tenant = request.url.path.split("/")[1]
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
                    "jwks_uri": f"https://login.microsoftonline.com/{tenant}/keys",
                },
            )
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(tenant_keys[tenant].public_key(), as_dict=True)
        return httpx.Response(200, json={"keys": [{**jwk, "kid": KEY_ID}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        validator = EntraTokenValidator(
            settings(auth_tenant_mode="organizations", auth_allowed_tenant_ids=""),
            http_client=client,
        )
        principals = []
        for tenant, key in tenant_keys.items():
            principals.append(
                await validator.validate(
                    create_token(
                        key, tid=tenant, iss=f"https://login.microsoftonline.com/{tenant}/v2.0"
                    )
                )
            )
        assert {principal.tenant_id for principal in principals} == {TENANT_ID, OTHER_TENANT_ID}
        assert principals[0].isolation_key != principals[1].isolation_key
        with pytest.raises(AuthenticationError):
            await validator.validate(
                create_token(
                    tenant_keys[TENANT_ID],
                    tid=OTHER_TENANT_ID,
                    iss=f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0",
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tenant",
    [
        "common",
        "organizations",
        "consumers",
        "../common",
        "9188040d-6c67-4c5b-b112-36a304b66dad",
        "not-a-tenant",
    ],
)
async def test_organizations_rejects_nonorganizational_tenants_before_discovery(
    private_key: rsa.RSAPrivateKey,
    tenant: str,
) -> None:
    validator, requests = create_validator(
        private_key,
        settings(
            auth_tenant_mode="organizations",
            auth_allowed_tenant_ids="",
        ),
    )
    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, tid=tenant))
    assert error.value.code == "tenant_not_allowed"
    assert requests == []


@pytest.mark.asyncio
async def test_organizations_explicit_allowlist_blocks_other_organization(
    private_key: rsa.RSAPrivateKey,
) -> None:
    validator, requests = create_validator(private_key, settings(auth_tenant_mode="organizations"))
    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, tid=OTHER_TENANT_ID))
    assert error.value.code == "tenant_not_allowed"
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "jwks_uri",
    [
        "https://attacker.example/keys",
        "http://login.microsoftonline.com/keys",
        "https://owner:secret@login.microsoftonline.com/keys",
        "https://login.microsoftonline.com.evil.example/keys",
    ],
)
async def test_untrusted_signing_key_locations_are_never_requested(
    private_key: rsa.RSAPrivateKey,
    jwks_uri: str,
) -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": jwks_uri})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        validator = EntraTokenValidator(settings(), http_client=client)
        with pytest.raises(AuthenticationError):
            await validator.validate(create_token(private_key))
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_discovery_cannot_substitute_a_different_tenant_issuer(
    private_key: rsa.RSAPrivateKey,
) -> None:
    wrong_issuer = f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0"
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": wrong_issuer,
                    "jwks_uri": "https://login.microsoftonline.com/keys",
                },
            )
        return httpx.Response(200, json={"keys": [{**jwk, "kid": KEY_ID}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthenticationError):
            await EntraTokenValidator(settings(), http_client=client).validate(
                create_token(private_key, iss=wrong_issuer)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [[], None, {"issuer": ISSUER, "jwks_uri": []}])
async def test_malformed_metadata_returns_authentication_error(
    private_key: rsa.RSAPrivateKey,
    metadata: object,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=metadata)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthenticationError):
            await EntraTokenValidator(settings(), http_client=client).validate(
                create_token(private_key)
            )


@pytest.mark.asyncio
async def test_even_injected_clients_cannot_follow_metadata_redirects(
    private_key: rsa.RSAPrivateKey,
) -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://attacker.example/keys"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        with pytest.raises(AuthenticationError):
            await EntraTokenValidator(settings(), http_client=client).validate(
                create_token(private_key)
            )
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"scp": ["access_as_user"]},
        {"azp": [FRONTEND_CLIENT_ID]},
        {"iat": float("inf")},
        {"exp": float("inf")},
    ],
)
async def test_malformed_claim_types_return_clean_authentication_error(
    private_key: rsa.RSAPrivateKey,
    claims: dict,
) -> None:
    validator, _ = create_validator(private_key)
    with pytest.raises(AuthenticationError) as error:
        await validator.validate(create_token(private_key, **claims))
    assert error.value.code == "invalid_token"


@pytest.mark.asyncio
async def test_inactive_tenant_metadata_is_refetched_after_large_organization_churn(
    private_key: rsa.RSAPrivateKey,
) -> None:
    first_tenant_discoveries = 0
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_tenant_discoveries
        tenant = request.url.path.split("/")[1]
        if request.url.path.endswith("openid-configuration"):
            if tenant == TENANT_ID:
                first_tenant_discoveries += 1
            return httpx.Response(
                200,
                json={
                    "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
                    "jwks_uri": f"https://login.microsoftonline.com/{tenant}/keys",
                },
            )
        return httpx.Response(200, json={"keys": [{**jwk, "kid": KEY_ID}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        validator = EntraTokenValidator(
            settings(
                auth_tenant_mode="organizations",
                auth_allowed_tenant_ids="",
            ),
            http_client=client,
        )
        token = create_token(private_key)
        await validator.validate(token)
        for index in range(1, 513):
            tenant = str(UUID(int=index))
            await validator.validate(
                create_token(
                    private_key,
                    tid=tenant,
                    iss=f"https://login.microsoftonline.com/{tenant}/v2.0",
                )
            )
        assert (await validator.validate(token)).tenant_id == TENANT_ID
    assert first_tenant_discoveries == 2
