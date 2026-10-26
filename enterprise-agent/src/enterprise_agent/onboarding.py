"""Offline configuration checks and a non-destructive local setup wizard."""

import getpass
import json
import os
import re
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from enterprise_agent.config import PLACEHOLDER_VALUES, Settings

_TOOLS = {
    "mock_read",
    "local_read",
    "artifact_generation",
    "workiq_read",
    "workiq_write",
    "fabric_read",
    "foundry_read",
}
_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


@dataclass(frozen=True)
class ConfigurationCheck:
    code: str
    level: str
    message: str


def configuration_report(settings: Settings) -> list[ConfigurationCheck]:
    """Check configured values only; never claim live consent/service verification."""
    checks = []

    def add(code: str, message: str, level: str = "error") -> None:
        checks.append(ConfigurationCheck(code, level, message))

    groups = settings.tool_groups
    if settings.runtime_mode == "gemini" and not settings.gemini_is_configured:
        add("gemini_key", "Set GEMINI_API_KEY. Run: uv run enterprise-agent setup")
    if groups - _TOOLS:
        add("tool_groups", "ENABLED_TOOL_GROUPS contains an unknown capability.")
    if settings.runtime_mode == "demo":
        add("demo", "Template demo only: no Gemini or Microsoft 365 retrieval.", "info")
    if groups & {"workiq_read", "workiq_write", "fabric_read", "foundry_read"}:
        if not settings.auth_enabled:
            add("authentication_required", "Enable AUTH_ENABLED for delegated Microsoft tools.")
    if settings.auth_enabled:
        try:
            tenant = str(UUID(settings.azure_tenant_id))
            backend = str(UUID(settings.azure_client_id))
            for allowed in settings.allowed_tenant_ids | settings.allowed_client_ids:
                UUID(allowed)
            if tenant == _PERSONAL_TENANT:
                raise ValueError("A work/school tenant is required")
        except ValueError:
            add(
                "entra_ids",
                "Use work/school tenant and application UUIDs; not common or wildcards.",
            )
        else:
            if tenant not in settings.allowed_tenant_ids:
                add("entra_tenant", "AZURE_TENANT_ID must be in AUTH_ALLOWED_TENANT_IDS.")
            if backend not in settings.entra_api_audiences:
                add("entra_audience", "ENTRA_API_AUDIENCE must include the backend application ID.")
        client = settings.entra_spa_client_id.strip()
        if client in PLACEHOLDER_VALUES and len(settings.allowed_client_ids) == 1:
            client = next(iter(settings.allowed_client_ids))
        try:
            client = str(UUID(client))
            if client not in settings.allowed_client_ids:
                raise ValueError("Client is not allowed")
        except ValueError:
            add("entra_client", "Set a valid SPA client ID in ENTRA_ALLOWED_CLIENT_IDS.")
        if not settings.required_scopes:
            add("entra_scope", "Set ENTRA_REQUIRED_SCOPES to the exposed delegated API scope.")
        redirect = urlsplit(settings.entra_spa_redirect_uri)
        if redirect.hostname in {"localhost", "127.0.0.1"}:
            port = redirect.port or (443 if redirect.scheme == "https" else 80)
            if port != settings.app_port:
                add("redirect_port", "APP_PORT and the registered SPA redirect port must match.")
        add(
            "consent",
            "Offline check only. Tenant enablement, consent, billing and file access "
            "must be confirmed by your organization's administrator.",
            "info",
        )
    if groups & {"workiq_read", "workiq_write"} and not settings.workiq_obo_is_configured:
        add("workiq_credentials", "Configure backend Entra credentials for Work IQ OBO.")
    if "fabric_read" in groups and not settings.fabric_obo_is_configured:
        add("fabric_credentials", "Configure Fabric endpoint and delegated backend credentials.")
    if "foundry_read" in groups and (
        not settings.foundry_iq_is_configured or not settings.foundry_iq_shared_knowledge
    ):
        add(
            "foundry_credentials",
            "Configure the shared Foundry knowledge source before enabling it.",
        )
    if settings.app_env != "development" and "local_read" in groups:
        add("local_environment", "Local folder imports require APP_ENV=development.")
    if settings.storage_backend != "database":
        add(
            "history",
            "Set STORAGE_BACKEND=database to keep chat history across restarts.",
            "warning",
        )
    if "mock_read" in groups and settings.runtime_mode != "demo":
        add("sample_data", "mock_read returns sample facts. Disable it for real work.", "warning")
    for code, path in (
        ("egress_policy", settings.egress_policy_path),
        ("approval_policy", settings.approval_policy_path),
        ("artifact_templates", settings.artifact_template_config_path),
    ):
        if not path.is_file():
            add(
                code,
                "A required configuration file is missing; restore config/ from the repository.",
            )
    return checks


def setup_environment(
    env_path: Path,
    *,
    input_fn: Callable[[str], str] | None = None,
    secret_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Create a private .env once; cancellation or existing files never replace credentials."""
    if env_path.exists() or env_path.is_symlink():
        output_fn("Existing .env preserved. Check it with: uv run enterprise-agent doctor")
        return 0
    if secret_fn is None and not sys.stdin.isatty():
        output_fn(
            "Setup needs an interactive terminal for hidden secret input. "
            "Alternatively copy .env.example to .env and edit it privately."
        )
        return 2
    ask, ask_secret = input_fn or input, secret_fn or getpass.getpass
    output_fn("Local Microsoft 365 setup: use your own organization's two Entra registrations.")
    output_fn(
        "Guide: docs/SELF_HOST_SETUP.md. Secret inputs are hidden; no provider calls are made."
    )

    def value(prompt: str, *, secret: bool = False, default: str = "") -> str:
        while True:
            with warnings.catch_warnings():
                # getpass warns before falling back to echoed input; stop that fallback.
                warnings.simplefilter("error", getpass.GetPassWarning)
                entered = (ask_secret if secret else ask)(prompt).strip() or default
            if (
                entered not in PLACEHOLDER_VALUES
                and not any(ord(char) < 32 for char in entered)
                and "${" not in entered
            ):
                return entered
            output_fn("Enter a non-empty value without control characters or variable expansion.")

    def app_id(prompt: str, *, different_from: str = "", tenant: bool = False) -> str:
        while True:
            try:
                entered = str(UUID(value(prompt)))
                if entered == different_from or (tenant and entered == _PERSONAL_TENANT):
                    raise ValueError("Use separate applications and a work/school tenant")
                return entered
            except ValueError:
                output_fn("Use the UUID from Entra Overview; backend and browser apps must differ.")

    try:
        key = value("Gemini API key (hidden): ", secret=True)
        while True:
            model = value("Gemini model [gemini-3.8-flash]: ", default="gemini-3.8-flash")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,150}", model):
                break
            output_fn("Enter a model ID available to your key, for example gemini-3.8-flash.")
        tenant = app_id("Directory (tenant) ID: ", tenant=True)
        backend = app_id("Backend Application (client) ID: ")
        frontend = app_id("Browser SPA Application (client) ID: ", different_from=backend)
        secret = value("Backend client secret VALUE (hidden, not Secret ID): ", secret=True)
    except (EOFError, KeyboardInterrupt):
        output_fn("Setup cancelled; no configuration was written.")
        return 130
    except getpass.GetPassWarning:
        output_fn(
            "Hidden input is unavailable. Use an interactive terminal or edit .env privately."
        )
        return 2
    values = {
        "APP_ENV": "development",
        "APP_HOST": "127.0.0.1",
        "APP_PORT": "8000",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "RUNTIME_MODE": "gemini",
        "GEMINI_API_KEY": key,
        "GEMINI_MODEL": model,
        "AUTH_ENABLED": "true",
        "AZURE_TENANT_ID": tenant,
        "AZURE_CLIENT_ID": backend,
        "AZURE_CLIENT_SECRET": secret,
        "ENTRA_API_AUDIENCE": backend,
        "ENTRA_REQUIRED_SCOPES": "access_as_user",
        "AUTH_ALLOWED_TENANT_IDS": tenant,
        "ENTRA_ALLOWED_CLIENT_IDS": frontend,
        "ENTRA_SPA_CLIENT_ID": frontend,
        "ENTRA_SPA_REDIRECT_URI": "http://localhost:8000/auth/callback",
        "ENABLED_TOOL_GROUPS": "workiq_read,local_read,artifact_generation",
        "STORAGE_BACKEND": "database",
        "SPEECH_PROVIDER": "disabled",
        "REQUIRE_WRITE_APPROVAL": "true",
    }
    content = "# Private local configuration. Never commit this file.\n" + "".join(
        f"{name}={json.dumps(setting, ensure_ascii=False)}\n" for name, setting in values.items()
    )
    created = False
    try:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        output_fn("An .env already exists; it was preserved.")
        return 2
    except OSError:
        if created:
            env_path.unlink(missing_ok=True)
        output_fn("Could not save .env; check directory permissions and available disk space.")
        return 2
    output_fn("Saved private .env. Next: uv run enterprise-agent doctor")
    output_fn("Then: uv run enterprise-agent start --open")
    return 0
