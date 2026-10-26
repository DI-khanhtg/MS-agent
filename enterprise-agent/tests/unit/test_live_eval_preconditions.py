"""A live evaluation must not run a demo or unauthenticated enterprise runtime."""

import importlib

import pytest

from enterprise_agent.config import Settings

EVALUATIONS = [
    ("evals.test_workiq_tool_selection_live", "test_gemini_routes_m365_requests_to_workiq"),
    ("evals.test_fabric_tool_selection_live", "test_gemini_routes_business_queries_to_fabric"),
    (
        "evals.test_multi_source_tool_selection_live",
        "test_gemini_combines_workiq_fabric_and_artifacts",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name,function_name", EVALUATIONS)
@pytest.mark.parametrize("mode,auth", [("demo", True), ("gemini", False)])
async def test_enterprise_live_evaluations_reject_inactive_runtime(
    monkeypatch, module_name, function_name, mode, auth
):
    module = importlib.import_module(module_name)
    for flag in ("RUN_WORKIQ_LIVE_EVALS", "RUN_FABRIC_LIVE_EVALS", "RUN_MULTI_SOURCE_LIVE_EVALS"):
        monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("WORKIQ_USER_ACCESS_TOKEN", "not-a-real-token")
    monkeypatch.setenv("FABRIC_USER_ACCESS_TOKEN", "not-a-real-token")
    groups = "workiq_read"
    if "workiq" not in module_name:
        groups += ",fabric_read,artifact_generation"
    settings = Settings(
        _env_file=None,
        runtime_mode=mode,
        auth_enabled=auth,
        enabled_tool_groups=groups,
        gemini_api_key="test-key",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids="tenant-a",
        azure_client_id="api-client",
        azure_client_secret="test-secret",
        fabric_workspace_id="22222222-2222-2222-2222-222222222222",
        fabric_data_agent_id="33333333-3333-3333-3333-333333333333",
    )
    monkeypatch.setattr(module, "Settings", lambda: settings)

    def no_network(*args, **kwargs):
        pytest.fail("Misconfigured evaluation must stop before token validation or API calls")

    monkeypatch.setattr(module, "EntraTokenValidator", no_network)
    with pytest.raises(pytest.skip.Exception, match="RUNTIME_MODE=gemini and AUTH_ENABLED=true"):
        await getattr(module, function_name)()
