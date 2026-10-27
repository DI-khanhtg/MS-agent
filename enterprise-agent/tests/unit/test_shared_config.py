import pytest

from enterprise_agent.config import Settings

HOME = "11111111-1111-1111-1111-111111111111"
OTHER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def shared(**overrides):
    values = dict(
        deployment_mode="shared",
        auth_tenant_mode="organizations",
        auth_enabled=True,
        auth_allowed_tenant_ids="",
        azure_tenant_id=HOME,
        public_base_url="https://agent.example.com",
        document_uploads_enabled=True,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_shared_organization_policy_accepts_independent_tenants():
    settings = shared()
    assert settings.tenant_is_allowed(HOME)
    assert settings.tenant_is_allowed(OTHER)
    assert settings.entra_is_configured is False  # An API audience is still required.


@pytest.mark.parametrize(
    "tenant",
    [
        "common",
        "organizations",
        "../common",
        "tenant/keys",
        "*",
        "not-a-uuid",
        "9188040d-6c67-4c5b-b112-36a304b66dad",
    ],
)
def test_organizations_policy_rejects_untrusted_or_personal_tenants(tenant):
    assert not shared().tenant_is_allowed(tenant)


def test_optional_organization_allowlist_is_enforced_and_local_policy_stays_closed():
    settings = shared(auth_allowed_tenant_ids=HOME)
    assert settings.tenant_is_allowed(HOME)
    assert not settings.tenant_is_allowed(OTHER)
    local = Settings(_env_file=None, azure_tenant_id=HOME)
    assert local.tenant_is_allowed(HOME)
    assert not local.tenant_is_allowed(OTHER)


def test_server_upload_access_requires_explicit_owner_opt_in_and_auth():
    settings = shared(app_env="production")
    assert settings.local_document_access_enabled
    assert not shared(
        app_env="production", document_uploads_enabled=False
    ).local_document_access_enabled
    assert not shared(app_env="production", auth_enabled=False).local_document_access_enabled
    assert not Settings(_env_file=None, app_env="production").local_document_access_enabled


def test_public_origin_is_canonical_and_not_an_arbitrary_callback_path():
    assert (
        shared(public_base_url="https://AGENT.example.com:443/").public_base_url
        == "https://agent.example.com"
    )
    with pytest.raises(ValueError):
        shared(public_base_url="https://agent.example.com/another-app")
    with pytest.raises(ValueError):
        shared(public_base_url="https://user:secret@agent.example.com")
