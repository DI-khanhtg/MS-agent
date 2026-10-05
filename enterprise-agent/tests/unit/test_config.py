import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.config import Settings


def test_deepseek_defaults_match_master_plan() -> None:
    settings = Settings(_env_file=None)

    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-flash"


def test_placeholder_key_is_not_configured() -> None:
    settings = Settings(
        _env_file=None,
        deepseek_api_key="PUT_YOUR_DEEPSEEK_API_KEY_HERE",
    )

    assert settings.deepseek_is_configured is False


def test_real_key_is_configured_without_exposing_it() -> None:
    settings = Settings(_env_file=None, deepseek_api_key="test-secret-key")

    assert settings.deepseek_is_configured is True
    assert "test-secret-key" not in repr(settings.deepseek_api_key)


def test_base_url_is_normalized() -> None:
    settings = Settings(_env_file=None, deepseek_base_url="https://api.deepseek.com///")

    assert settings.deepseek_base_url == "https://api.deepseek.com"


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_timeout_seconds=0)


def test_auth_and_obo_configuration_are_reported_separately() -> None:
    auth_only = Settings(
        _env_file=None,
        entra_api_audience="api-client",
        auth_allowed_tenant_ids="tenant-a",
    )
    full_obo = Settings(
        _env_file=None,
        entra_api_audience="api-client",
        auth_allowed_tenant_ids="tenant-a",
        azure_client_id="api-client",
        azure_client_secret="client-secret",
    )

    assert auth_only.entra_is_configured is True
    assert auth_only.workiq_obo_is_configured is False
    assert full_obo.workiq_obo_is_configured is True


def test_comma_separated_security_allowlists_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        auth_allowed_tenant_ids="tenant-a, tenant-b",
        entra_allowed_client_ids="client-a, client-b",
        enabled_tool_groups="workiq_read, mock_read",
    )

    assert settings.allowed_tenant_ids == {"tenant-a", "tenant-b"}
    assert settings.allowed_client_ids == {"client-a", "client-b"}
    assert settings.tool_groups == {"mock_read", "workiq_read"}


def test_artifact_tool_requires_supported_manifest_and_entrypoint(tmp_path: Path) -> None:
    package = tmp_path / "@oai" / "artifact-tool"
    entrypoint = package / "dist" / "node" / "artifact_tool.mjs"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("export {};", encoding="utf-8")
    (package / "package.json").write_text(
        json.dumps({"name": "@oai/artifact-tool", "version": "2.8.0"}),
        encoding="utf-8",
    )

    configured = Settings(_env_file=None, artifact_tool_node_modules=tmp_path)

    assert configured.artifact_tool_entrypoint == entrypoint


def test_fabric_endpoint_is_derived_from_valid_ids() -> None:
    settings = Settings(
        _env_file=None,
        fabric_workspace_id="22222222-2222-2222-2222-222222222222",
        fabric_data_agent_id="33333333-3333-3333-3333-333333333333",
    )

    assert settings.fabric_mcp_endpoint == (
        "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
        "22222222-2222-2222-2222-222222222222/dataagents/"
        "33333333-3333-3333-3333-333333333333/agent"
    )


def test_fabric_configuration_rejects_invalid_ids_scope_and_endpoint() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, fabric_workspace_id="not-a-guid")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, fabric_scope="https://graph.microsoft.com/.default")

    settings = Settings(
        _env_file=None,
        fabric_mcp_url="https://attacker.example/v1/mcp/workspaces/x/dataagents/y/agent",
    )
    assert settings.fabric_mcp_endpoint is None

    for unsafe_url in (
        "https://user@api.fabric.microsoft.com/v1/mcp/workspaces/x/dataagents/y/agent",
        "https://api.fabric.microsoft.com:8443/v1/mcp/workspaces/x/dataagents/y/agent",
        "https://api.fabric.microsoft.com/v1/mcp/workspaces/x/dataagents/y/agent?q=1",
    ):
        assert Settings(_env_file=None, fabric_mcp_url=unsafe_url).fabric_mcp_endpoint is None


def test_fabric_obo_status_requires_endpoint_and_entra_credentials() -> None:
    settings = Settings(
        _env_file=None,
        entra_api_audience="api-client",
        auth_allowed_tenant_ids="tenant-a",
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        fabric_workspace_id="22222222-2222-2222-2222-222222222222",
        fabric_data_agent_id="33333333-3333-3333-3333-333333333333",
    )

    assert settings.fabric_obo_is_configured is True
