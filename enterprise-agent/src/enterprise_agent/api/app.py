"""FastAPI application factory."""

import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from enterprise_agent.actions.durable import DurableApprovalStore
from enterprise_agent.actions.executor import WorkIQActionExecutor
from enterprise_agent.actions.policy import ApprovalPolicy
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.actions.store import InMemoryApprovalStore
from enterprise_agent.actions.tools import ControlledActionTools
from enterprise_agent.agent.demo import DemoRuntime
from enterprise_agent.agent.runtime import AgentRuntime, MicrosoftAgentRuntime
from enterprise_agent.api.actions import router as actions_router
from enterprise_agent.api.artifacts import router as artifacts_router
from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.api.auth import router as auth_router
from enterprise_agent.api.chat import router as chat_router
from enterprise_agent.api.limits import BodyLimitMiddleware
from enterprise_agent.api.local_documents import router as local_documents_router
from enterprise_agent.api.schemas import HealthResponse
from enterprise_agent.api.sessions import router as sessions_router
from enterprise_agent.api.voice import router as voice_router
from enterprise_agent.artifacts.renderers import create_renderer_registry
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.artifacts.store import ArtifactStore
from enterprise_agent.artifacts.templates import TemplateCatalog
from enterprise_agent.artifacts.tools import ArtifactTools
from enterprise_agent.config import Settings, get_settings
from enterprise_agent.hosting import ChatCapacity, shared_configuration_errors
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.token_validation import EntraTokenValidator, TokenValidator
from enterprise_agent.storage.conversations import ConversationStore
from enterprise_agent.storage.database import Database
from enterprise_agent.telemetry.operations import AuditLog, RateLimiter, audit_sink
from enterprise_agent.telemetry.tracing import configure_tracing, request_span
from enterprise_agent.tools.document_reader import SUPPORTED
from enterprise_agent.tools.fabric.auth import MsalFabricOnBehalfOfTokenProvider
from enterprise_agent.tools.fabric.client import FabricDataAgentClient
from enterprise_agent.tools.fabric.tools import FabricReadTools
from enterprise_agent.tools.knowledge.client import KnowledgeTools
from enterprise_agent.tools.local.documents import LocalDocumentTools
from enterprise_agent.tools.registry import create_default_registry
from enterprise_agent.tools.workiq.auth import (
    MsalGraphFileTokenProvider,
    MsalOnBehalfOfTokenProvider,
)
from enterprise_agent.tools.workiq.client import WorkIQMCPClient
from enterprise_agent.tools.workiq.tools import WorkIQReadTools
from enterprise_agent.voice.transcriber import AzureSpeechTranscriber, Transcriber


def create_app(
    *,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    token_validator: TokenValidator | None = None,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    shared_errors = shared_configuration_errors(app_settings)
    if shared_errors:
        raise ValueError(" ".join(shared_errors))
    if app_settings.app_env in {"staging", "production"}:
        if (
            not app_settings.auth_enabled
            or app_settings.runtime_mode == "demo"
            or "mock_read" in app_settings.tool_groups
            or (
                "local_read" in app_settings.tool_groups
                and not app_settings.local_document_access_enabled
            )
            or not app_settings.require_write_approval
            or app_settings.storage_backend != "database"
        ):
            raise ValueError(
                "Staging/production require auth, durable storage, live tools and approval"
            )
    logging.basicConfig(
        level=getattr(logging, app_settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    application = FastAPI(
        title="Enterprise Agent API",
        version="0.3.0",
        description="Gemini enterprise agent with governed data, artifacts, and actions",
    )
    application.state.settings = app_settings
    database = Database(app_settings) if app_settings.storage_backend == "database" else None
    application.state.database = database
    application.state.conversations = ConversationStore(database, app_settings.max_history_messages)
    application.state.audit = AuditLog(app_settings.audit_log_path)
    configure_tracing(app_settings.telemetry_enabled)
    application.state.limiter = RateLimiter(app_settings.rate_limit_per_minute)
    application.state.shared_request_limiter = RateLimiter(app_settings.shared_requests_per_minute)
    application.state.shared_chat_limiter = RateLimiter(
        app_settings.shared_chat_requests_per_minute
    )
    application.state.chat_capacity = ChatCapacity(
        app_settings.shared_max_concurrent_chats, app_settings.shared_max_concurrent_per_user
    )
    application.state.egress = EgressGate.from_yaml(app_settings.egress_policy_path)
    local_tools = LocalDocumentTools(app_settings, application.state.egress)
    application.state.local_document_tools = local_tools
    application.state.transcriber = transcriber or AzureSpeechTranscriber(app_settings)
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[x.strip() for x in app_settings.allowed_hosts.split(",") if x.strip()],
    )
    application.add_middleware(BodyLimitMiddleware)
    application.state.token_validator = token_validator or EntraTokenValidator(app_settings)
    workiq_client = WorkIQMCPClient(app_settings)
    workiq_token_provider = MsalOnBehalfOfTokenProvider(app_settings)
    approval_policy = ApprovalPolicy.from_yaml(app_settings.approval_policy_path)
    approval_service = ApprovalService(
        DurableApprovalStore(approval_policy.expiry_minutes, database)
        if database
        else InMemoryApprovalStore(approval_policy.expiry_minutes),
        approval_policy,
        WorkIQActionExecutor(
            workiq_client,
            workiq_token_provider,
            enabled=app_settings.runtime_mode == "gemini"
            and "workiq_write" in app_settings.tool_groups
            and app_settings.require_write_approval,
        ),
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
        egress_gate = application.state.egress
        workiq_tools = WorkIQReadTools(
            workiq_client,
            workiq_token_provider,
            egress_gate,
            graph_token_provider=MsalGraphFileTokenProvider(app_settings),
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
                local_tools=local_tools,
                knowledge_tools=KnowledgeTools(app_settings, egress_gate),
            ),
            egress_gate=egress_gate,
            database=database,
        )
        if app_settings.runtime_mode == "demo":
            runtime = DemoRuntime(app_settings, local_tools, artifact_service)
    application.state.agent_runtime = runtime
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(artifacts_router)
    application.include_router(actions_router)
    application.include_router(sessions_router)
    application.include_router(voice_router)
    application.include_router(local_documents_router)
    static_root = Path(__file__).resolve().parents[1] / "ui"
    application.mount("/static", StaticFiles(directory=static_root), name="static")

    @application.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_root / "index.html", headers={"Cache-Control": "no-store"})

    @application.get("/chat/{session_id}", include_in_schema=False)
    async def chat_page(session_id: str):
        if len(session_id) > 128:
            raise HTTPException(404, "Page not found")
        # This is only the public shell. Session data remains behind the owner-scoped API.
        return FileResponse(static_root / "index.html", headers={"Cache-Control": "no-store"})

    @application.get("/auth/callback", include_in_schema=False)
    async def auth_callback():
        return FileResponse(
            static_root / "auth-callback.html", headers={"Cache-Control": "no-store"}
        )

    @application.middleware("http")
    async def request_observability(request: Request, call_next):
        started = time.monotonic()
        request.state.request_id = str(uuid4())
        shared = app_settings.deployment_mode == "shared"
        if shared and request.url.path.startswith("/api/"):
            if not await application.state.shared_request_limiter.allow("all"):
                return JSONResponse(
                    {"detail": "Service request limit reached. Try again shortly."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        # Block browser cross-origin writes, even in unauthenticated local development.
        origin = request.headers.get("origin")
        if (
            origin
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin.rstrip("/")
            != (app_settings.public_base_url if shared else str(request.base_url).rstrip("/"))
        ):
            return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if length > 2_500_000:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        with request_span() as span:
            span.set_attribute("http.request.method", request.method)
            token = audit_sink.set(application.state.audit)
            try:
                response = await call_next(request)
            finally:
                audit_sink.reset(token)
            span.set_attribute("http.response.status_code", response.status_code)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if shared:
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            if app_settings.public_base_url.startswith("https://"):
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
        microsoft_sources = (
            " https://login.microsoftonline.com" if app_settings.auth_enabled else ""
        )
        ancestors = "'self'" if request.url.path == "/auth/callback" else "'none'"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            f"connect-src 'self'{microsoft_sources}; frame-src 'self'{microsoft_sources}; "
            f"img-src 'self' data:; frame-ancestors {ancestors}; base-uri 'self'; object-src 'none'"
        )
        application.state.audit.emit(
            "http_request",
            request_id=request.state.request_id,
            status=response.status_code,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response

    @application.get("/api/config", tags=["operations"])
    async def configuration(principal: PrincipalDependency):
        return {
            "runtime_mode": app_settings.runtime_mode,
            "auth_enabled": app_settings.auth_enabled,
            "storage_backend": app_settings.storage_backend,
            "speech_configured": app_settings.speech_is_configured,
            "foundry_iq_configured": app_settings.foundry_iq_is_configured,
            "tool_groups": sorted(app_settings.tool_groups),
            "artifact_formats": ["docx", "pdf", "xlsx", "pptx", "markdown", "json"],
            "document_extensions": sorted(SUPPORTED),
            "deployment_mode": app_settings.deployment_mode,
            "document_uploads_enabled": (
                app_settings.auth_enabled
                and app_settings.local_document_access_enabled
                and "local_read" in app_settings.tool_groups
            ),
        }

    @application.get("/api/metrics", tags=["operations"])
    async def metrics(principal: PrincipalDependency):
        if app_settings.auth_enabled and "metrics.read" not in principal.scopes:
            return JSONResponse({"detail": "metrics.read scope required"}, status_code=403)
        totals = dict(application.state.audit.totals)
        estimate = None
        if (
            app_settings.input_price_per_million is not None
            and app_settings.output_price_per_million is not None
        ):
            estimate = (
                totals.get("input_tokens", 0) * app_settings.input_price_per_million
                + totals.get("output_tokens", 0) * app_settings.output_price_per_million
            ) / 1_000_000
        return {
            **totals,
            "estimated_model_cost": estimate,
            "currency": app_settings.cost_currency,
            "cost_scope": "process_lifetime_model_tokens_only",
        }

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(
            environment=app_settings.app_env,
            model=app_settings.gemini_model,
            gemini_configured=app_settings.gemini_is_configured,
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
