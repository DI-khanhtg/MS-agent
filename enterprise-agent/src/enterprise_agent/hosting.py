"""Shared-service startup policy and bounded in-process chat admission."""

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request

from enterprise_agent.config import PLACEHOLDER_VALUES, Settings


def shared_configuration_errors(settings: Settings) -> list[str]:
    if settings.deployment_mode != "shared":
        return []
    errors = []
    if not settings.auth_enabled:
        errors.append("Shared hosting requires AUTH_ENABLED=true.")
    if settings.runtime_mode != "gemini" or not settings.gemini_is_configured:
        errors.append("Shared hosting requires configured Gemini credentials on the server.")
    if settings.storage_backend != "database":
        errors.append("Shared hosting requires STORAGE_BACKEND=database and persistent storage.")
    if not settings.require_write_approval or "mock_read" in settings.tool_groups:
        errors.append("Shared hosting requires approval enforcement and forbids mock_read.")
    try:
        UUID(settings.azure_tenant_id)
        backend = str(UUID(settings.azure_client_id))
        for client in settings.allowed_client_ids:
            UUID(client)
        for tenant in settings.auth_allowed_tenant_ids.split(","):
            if tenant.strip() not in PLACEHOLDER_VALUES:
                UUID(tenant.strip())
        if not settings.allowed_client_ids:
            raise ValueError("An approved browser client is required")
        client = settings.entra_spa_client_id
        if client in PLACEHOLDER_VALUES and len(settings.allowed_client_ids) == 1:
            client = next(iter(settings.allowed_client_ids))
        if client not in settings.allowed_client_ids or backend not in settings.entra_api_audiences:
            raise ValueError("The API audience and approved client must be consistent")
    except ValueError:
        errors.append("Configure valid Entra tenant/backend/approved SPA IDs and API audience.")
    if not settings.required_scopes:
        errors.append("Shared hosting requires a delegated API scope.")
    if (
        settings.tool_groups & {"workiq_read", "workiq_write"}
        and not settings.workiq_obo_is_configured
    ):
        errors.append("Shared Work IQ requires delegated backend credentials on the server.")
    origin = urlsplit(settings.public_base_url)
    if not origin.hostname:
        errors.append("Set PUBLIC_BASE_URL to the service's public origin.")
    else:
        if settings.app_env != "development" and (
            origin.scheme != "https" or origin.hostname in {"localhost", "127.0.0.1"}
        ):
            errors.append("Shared staging/production requires a public HTTPS origin.")
        if settings.entra_spa_redirect_uri != settings.public_base_url + "/auth/callback":
            errors.append("ENTRA_SPA_REDIRECT_URI must match PUBLIC_BASE_URL/auth/callback.")
        hosts = {host.strip().lower() for host in settings.allowed_hosts.split(",")}
        if "*" in hosts or origin.hostname not in hosts:
            errors.append("ALLOWED_HOSTS must explicitly include the public hostname; no wildcard.")
    if "local_read" in settings.tool_groups and not settings.local_document_access_enabled:
        errors.append("Shared file tools require AUTH_ENABLED and DOCUMENT_UPLOADS_ENABLED=true.")
    if os.environ.get("WEB_CONCURRENCY", "1") != "1":
        errors.append(
            "This shared-service starter requires one worker/replica (WEB_CONCURRENCY=1)."
        )
    return errors


class ChatCapacity:
    """Nonblocking admission, with synchronous cleanup even during async cancellation."""

    def __init__(self, total: int, per_user: int):
        self.total, self.per_user = total, per_user
        self.active = 0
        self.users: dict[str, int] = defaultdict(int)
        self.lock = Lock()

    def acquire(self, owner: str) -> bool:
        with self.lock:
            if self.active >= self.total or self.users.get(owner, 0) >= self.per_user:
                return False
            self.active += 1
            self.users[owner] += 1
            return True

    def release(self, owner: str) -> None:
        with self.lock:
            self.active -= 1
            self.users[owner] -= 1
            if not self.users[owner]:
                del self.users[owner]


async def check_chat_rate(request: Request) -> None:
    if request.app.state.settings.deployment_mode == "shared":
        if not await request.app.state.shared_chat_limiter.allow("all"):
            raise HTTPException(
                429,
                "Shared chat request limit reached. Try again shortly.",
                headers={"Retry-After": "60"},
            )


@asynccontextmanager
async def chat_slot(request: Request, owner: str):
    if request.app.state.settings.deployment_mode != "shared":
        yield True
        return
    acquired = request.app.state.chat_capacity.acquire(owner)
    try:
        yield acquired
    finally:
        if acquired:
            request.app.state.chat_capacity.release(owner)
