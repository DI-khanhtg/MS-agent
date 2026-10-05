"""FastAPI application factory."""

import logging
from typing import Any

from fastapi import FastAPI

from enterprise_agent.actions.executor import WorkIQActionExecutor
from enterprise_agent.actions.policy import ApprovalPolicy
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.actions.store import InMemoryApprovalStore
from enterprise_agent.actions.tools import ControlledActionTools
from enterprise_agent.agent.runtime import AgentRuntime, MicrosoftAgentRuntime
from enterprise_agent.api.actions import router as actions_router
from enterprise_agent.api.artifacts import router as artifacts_router
from enterprise_agent.api.auth import router as auth_router
from enterprise_agent.api.chat import router as chat_router
from enterprise_agent.api.schemas import HealthResponse
from enterprise_agent.artifacts.renderers import create_renderer_registry
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.artifacts.store import ArtifactStore
from enterprise_agent.artifacts.templates import TemplateCatalog
from enterprise_agent.artifacts.tools import ArtifactTools
from enterprise_agent.config import Settings, get_settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.token_validation import EntraTokenValidator, TokenValidator
from enterprise_agent.tools.fabric.auth import MsalFabricOnBehalfOfTokenProvider
from enterprise_agent.tools.fabric.client import FabricDataAgentClient
from enterprise_agent.tools.fabric.tools import FabricReadTools
from enterprise_agent.tools.registry import create_default_registry
from enterprise_agent.tools.workiq.auth import MsalOnBehalfOfTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQMCPClient
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


def create_app(
    *,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    token_validator: TokenValidator | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, app_settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    application = FastAPI(
        title="Enterprise Agent API",
        version="0.3.0",
        description="DeepSeek enterprise agent with governed data, artifacts, and actions",
    )
    application.state.settings = app_settings
    application.state.token_validator = token_validator or EntraTokenValidator(app_settings)
    workiq_client = WorkIQMCPClient(app_settings)
    workiq_token_provider = MsalOnBehalfOfTokenProvider(app_settings)
    approval_policy = ApprovalPolicy.from_yaml(app_settings.approval_policy_path)
    approval_service = ApprovalService(
        InMemoryApprovalStore(approval_policy.expiry_minutes),
        approval_policy,
        WorkIQActionExecutor(workiq_client, workiq_token_provider),
    )
    application.state.approval_service = approval_service
    artifact_service = ArtifactService(
        ArtifactStore(app_settings.artifact_storage_path),
        TemplateCatalog.from_yaml(
            app_settings.artifact_template_config_path,
            app_settings.artifact_template_root,
        ),
        create_renderer_registry(
            node_executable=app_settings.artifact_node_executable,
            artifact_tool_entrypoint=app_settings.artifact_tool_entrypoint,
            timeout_seconds=app_settings.artifact_render_timeout_seconds,
        ),
    )
    application.state.artifact_service = artifact_service
    if runtime is None:
        egress_gate = EgressGate.from_yaml(app_settings.egress_policy_path)
        workiq_tools = WorkIQReadTools(
            workiq_client,
            workiq_token_provider,
            egress_gate,
        )
        fabric_tools = FabricReadTools(
            FabricDataAgentClient(app_settings),
            MsalFabricOnBehalfOfTokenProvider(app_settings),
            egress_gate,
        )
        runtime = MicrosoftAgentRuntime(
            app_settings,
            registry=create_default_registry(
                workiq_tools=workiq_tools,
                artifact_tools=ArtifactTools(artifact_service),
                fabric_tools=fabric_tools,
                action_tools=ControlledActionTools(approval_service),
            ),
            egress_gate=egress_gate,
        )
    application.state.agent_runtime = runtime
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(artifacts_router)
    application.include_router(actions_router)

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(
            environment=app_settings.app_env,
            model=app_settings.deepseek_model,
            deepseek_configured=app_settings.deepseek_is_configured,
            auth_enabled=app_settings.auth_enabled,
            entra_configured=app_settings.entra_is_configured,
            workiq_obo_configured=app_settings.workiq_obo_is_configured,
            fabric_obo_configured=app_settings.fabric_obo_is_configured,
            artifact_tool_configured=app_settings.artifact_tool_entrypoint is not None,
            write_approval_required=app_settings.require_write_approval,
            enabled_tool_groups=sorted(app_settings.tool_groups),
        )

    return application


app: Any = create_app()
