"""Typed application configuration loaded from the local .env file."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_VALUES = {"", "PUT_YOUR_GEMINI_API_KEY_HERE", "TO_BE_FILLED_LATER"}


class Settings(BaseSettings):
    """Runtime settings.

    Extra values are intentionally ignored so later-phase placeholders can live in the same
    .env file without coupling implemented phases to future integrations.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    deployment_mode: Literal["local", "shared"] = "local"
    public_base_url: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    runtime_mode: Literal["gemini", "demo"] = "gemini"
    storage_backend: Literal["memory", "database"] = "memory"
    database_url: SecretStr = SecretStr("TO_BE_FILLED_LATER")
    database_path: Path = PROJECT_ROOT / "data" / "agent.db"
    local_documents_root: Path = PROJECT_ROOT / "data" / "documents"
    local_max_file_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    local_max_files: int = Field(default=200, ge=1, le=1000)
    max_tool_calls: int = Field(default=24, ge=1, le=100)
    max_history_messages: int = Field(default=40, ge=4, le=200)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    shared_requests_per_minute: int = Field(default=600, ge=1, le=100000)
    shared_chat_requests_per_minute: int = Field(default=120, ge=1, le=10000)
    shared_max_concurrent_chats: int = Field(default=8, ge=1, le=100)
    shared_max_concurrent_per_user: int = Field(default=2, ge=1, le=20)
    document_uploads_enabled: bool = False
    circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    circuit_reset_seconds: float = Field(default=30, gt=0, le=600)
    audit_log_path: Path = PROJECT_ROOT / "data" / "audit.jsonl"
    telemetry_enabled: bool = False
    speech_provider: Literal["disabled", "azure"] = "disabled"
    speech_key: SecretStr = SecretStr("TO_BE_FILLED_LATER")
    speech_region: str = "TO_BE_FILLED_LATER"
    speech_language: str = "vi-VN"
    speech_max_seconds: int = Field(default=60, ge=1, le=60)
    speech_timeout_seconds: float = Field(default=70, gt=0, le=120)
    foundry_iq_mcp_url: str = "TO_BE_FILLED_LATER"
    foundry_iq_api_key: SecretStr = SecretStr("TO_BE_FILLED_LATER")
    foundry_iq_auth_mode: Literal["entra", "api_key"] = "entra"
    foundry_iq_shared_knowledge: bool = False

    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = Field(default=60, gt=0, le=300)
    gemini_max_retries: int = Field(default=2, ge=0, le=5)
    # Optional second key for tool-less specialist sub-agents (reviewer, writer). Any
    # OpenAI-compatible endpoint works; the base URL and model default to the main agent's.
    subagent_enabled: bool = True
    subagent_api_key: SecretStr = SecretStr("")
    subagent_base_url: str = ""
    subagent_model: str = ""
    subagent_timeout_seconds: float = Field(default=45, gt=0, le=300)
    subagent_max_retries: int = Field(default=1, ge=0, le=5)
    subagent_max_calls_per_turn: int = Field(default=3, ge=1, le=10)
    subagent_max_input_chars: int = Field(default=60_000, ge=1000, le=500_000)
    agent_turn_timeout_seconds: float = Field(default=300, gt=0, le=1800)
    agent_synthesis_reserve_seconds: float = Field(default=15, ge=0)
    # Administrator-supplied rates; omitted until a current provider tariff is confirmed.
    input_price_per_million: float | None = Field(default=None, ge=0, le=10000)
    output_price_per_million: float | None = Field(default=None, ge=0, le=10000)
    cost_currency: str = Field(default="USD", min_length=3, max_length=3)

    auth_enabled: bool = False
    auth_tenant_mode: Literal["allowlist", "organizations"] = "allowlist"
    azure_tenant_id: str = "TO_BE_FILLED_LATER"
    azure_client_id: str = "TO_BE_FILLED_LATER"
    azure_client_secret: SecretStr = SecretStr("TO_BE_FILLED_LATER")
    entra_api_audience: str = "TO_BE_FILLED_LATER"
    entra_required_scopes: str = "access_as_user"
    auth_allowed_tenant_ids: str = "TO_BE_FILLED_LATER"
    entra_allowed_client_ids: str = "TO_BE_FILLED_LATER"
    entra_spa_client_id: str = "TO_BE_FILLED_LATER"
    entra_spa_redirect_uri: str = "http://localhost:8000/auth/callback"

    workiq_mcp_url: str = "https://workiq.svc.cloud.microsoft/mcp"
    workiq_scope: str = "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask"
    workiq_timeout_seconds: float = Field(default=30, gt=0, le=120)
    workiq_max_retries: int = Field(default=2, ge=0, le=5)

    fabric_workspace_id: str = "TO_BE_FILLED_LATER"
    fabric_data_agent_id: str = "TO_BE_FILLED_LATER"
    fabric_mcp_url: str = "TO_BE_FILLED_LATER"
    fabric_scope: str = "https://api.fabric.microsoft.com/.default"
    fabric_timeout_seconds: float = Field(default=120, gt=0, le=600)
    fabric_max_retries: int = Field(default=2, ge=0, le=5)

    enabled_tool_groups: str = "mock_read,artifact_generation"
    require_write_approval: bool = True
    egress_policy_path: Path = PROJECT_ROOT / "config" / "egress.yaml"
    approval_policy_path: Path = PROJECT_ROOT / "config" / "approvals.yaml"
    artifact_storage_path: Path = PROJECT_ROOT / "data" / "artifacts"
    artifact_template_config_path: Path = PROJECT_ROOT / "config" / "artifacts.yaml"
    artifact_template_root: Path = PROJECT_ROOT / "templates" / "artifacts"
    artifact_tool_node_modules: Path | None = None
    artifact_node_executable: str = "node"
    artifact_render_timeout_seconds: float = Field(default=60, gt=0, le=300)

    @model_validator(mode="after")
    def validate_turn_budget(self) -> "Settings":
        if self.agent_synthesis_reserve_seconds >= self.agent_turn_timeout_seconds:
            raise ValueError("Synthesis reserve must be smaller than the agent turn timeout")
        return self

    @field_validator("gemini_base_url", "subagent_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("public_base_url")
    @classmethod
    def normalize_public_origin(cls, value: str) -> str:
        value = value.strip()
        if value in PLACEHOLDER_VALUES:
            return ""
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin or loopback HTTP origin")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
        suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
        return f"{parsed.scheme}://{host}{suffix}"

    @field_validator("entra_spa_redirect_uri")
    @classmethod
    def validate_spa_redirect(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/auth/callback"
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
        ):
            raise ValueError("SPA redirect must use HTTPS or loopback HTTP at /auth/callback")
        # Accessing port also rejects malformed port values.
        _ = parsed.port
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("fabric_workspace_id", "fabric_data_agent_id")
    @classmethod
    def validate_fabric_id(cls, value: str) -> str:
        normalized = value.strip()
        if normalized in PLACEHOLDER_VALUES:
            return normalized
        try:
            return str(UUID(normalized))
        except ValueError as exc:
            raise ValueError("Fabric workspace and data agent IDs must be UUIDs") from exc

    @field_validator("fabric_scope")
    @classmethod
    def validate_fabric_scope(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "https://api.fabric.microsoft.com/.default":
            raise ValueError("FABRIC_SCOPE must target the Microsoft Fabric API")
        return normalized

    @field_validator(
        "egress_policy_path",
        "approval_policy_path",
        "artifact_storage_path",
        "artifact_template_config_path",
        "artifact_template_root",
        "artifact_tool_node_modules",
        "database_path",
        "local_documents_root",
        "audit_log_path",
    )
    @classmethod
    def resolve_project_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def gemini_is_configured(self) -> bool:
        return self.gemini_api_key.get_secret_value().strip() not in PLACEHOLDER_VALUES

    @property
    def subagent_is_configured(self) -> bool:
        return (
            self.subagent_enabled
            and self.subagent_api_key.get_secret_value().strip() not in PLACEHOLDER_VALUES
        )

    @property
    def subagent_endpoint(self) -> str:
        return self.subagent_base_url or self.gemini_base_url

    @property
    def subagent_model_name(self) -> str:
        return self.subagent_model.strip() or self.gemini_model

    @property
    def speech_is_configured(self) -> bool:
        return bool(
            self.speech_provider == "azure"
            and self.speech_key.get_secret_value() not in PLACEHOLDER_VALUES
            and self.speech_region not in PLACEHOLDER_VALUES
        )

    @property
    def foundry_iq_is_configured(self) -> bool:
        return bool(
            self.foundry_iq_mcp_url not in PLACEHOLDER_VALUES
            and (
                self.workiq_obo_is_configured
                if self.foundry_iq_auth_mode == "entra"
                else self.foundry_iq_api_key.get_secret_value() not in PLACEHOLDER_VALUES
            )
        )

    @property
    def entra_api_audiences(self) -> set[str]:
        configured = _csv_set(self.entra_api_audience)
        if configured:
            return configured
        if self.azure_client_id not in PLACEHOLDER_VALUES:
            return {self.azure_client_id, f"api://{self.azure_client_id}"}
        return set()

    @property
    def allowed_tenant_ids(self) -> set[str]:
        configured = _csv_set(self.auth_allowed_tenant_ids)
        if configured:
            return configured
        if self.azure_tenant_id not in PLACEHOLDER_VALUES:
            return {self.azure_tenant_id}
        return set()

    def tenant_is_allowed(self, tenant_id: str) -> bool:
        """Only organization UUIDs enter multitenant discovery; local defaults stay closed."""
        if self.auth_tenant_mode == "allowlist":
            return tenant_id in self.allowed_tenant_ids
        try:
            canonical = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            return False
        if canonical != tenant_id or canonical == "9188040d-6c67-4c5b-b112-36a304b66dad":
            return False
        approved = _csv_set(self.auth_allowed_tenant_ids)
        return not approved or canonical in approved

    @property
    def local_document_access_enabled(self) -> bool:
        if self.deployment_mode == "shared":
            return self.auth_enabled and self.document_uploads_enabled
        return self.app_env == "development"

    @property
    def required_scopes(self) -> set[str]:
        return _csv_set(self.entra_required_scopes)

    @property
    def allowed_client_ids(self) -> set[str]:
        return _csv_set(self.entra_allowed_client_ids)

    @property
    def tool_groups(self) -> set[str]:
        """Enabled groups; sub-agents follow their key so a missing key never breaks chat."""
        groups = _csv_set(self.enabled_tool_groups) - {"subagents"}
        if self.subagent_is_configured and self.runtime_mode == "gemini":
            groups.add("subagents")
        return groups

    @property
    def entra_is_configured(self) -> bool:
        return bool(
            self.entra_api_audiences
            and (self.auth_tenant_mode == "organizations" or self.allowed_tenant_ids)
        )

    @property
    def workiq_obo_is_configured(self) -> bool:
        return bool(
            self.entra_is_configured
            and self.azure_client_id not in PLACEHOLDER_VALUES
            and self.azure_client_secret.get_secret_value().strip() not in PLACEHOLDER_VALUES
        )

    @property
    def fabric_mcp_endpoint(self) -> str | None:
        configured = self.fabric_mcp_url.strip().rstrip("/")
        if configured not in PLACEHOLDER_VALUES:
            if not _is_valid_fabric_endpoint(configured):
                return None
            return configured
        if (
            self.fabric_workspace_id in PLACEHOLDER_VALUES
            or self.fabric_data_agent_id in PLACEHOLDER_VALUES
        ):
            return None
        return (
            "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
            f"{self.fabric_workspace_id}/dataagents/{self.fabric_data_agent_id}/agent"
        )

    @property
    def fabric_obo_is_configured(self) -> bool:
        return bool(
            self.fabric_mcp_endpoint
            and self.entra_is_configured
            and self.azure_client_id not in PLACEHOLDER_VALUES
            and self.azure_client_secret.get_secret_value().strip() not in PLACEHOLDER_VALUES
        )

    @property
    def artifact_tool_entrypoint(self) -> Path | None:
        node_modules = self.artifact_tool_node_modules or _default_artifact_tool_node_modules()
        package = node_modules / "@oai" / "artifact-tool"
        try:
            manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if manifest.get("name") != "@oai/artifact-tool" or _version_tuple(
            str(manifest.get("version", "0"))
        ) < (2, 7, 3):
            return None
        for relative in ("dist/node/artifact_tool.mjs", "dist/artifact_tool.mjs"):
            candidate = package / relative
            if candidate.is_file():
                return candidate
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # The demo launcher disables .env only for its process (including reload children).
    env_file = os.environ.get("ENTERPRISE_AGENT_ENV_FILE")
    return Settings() if env_file is None else Settings(_env_file=env_file or None)


def _csv_set(value: str) -> set[str]:
    return {
        item.strip()
        for item in value.split(",")
        if item.strip() and item.strip() not in PLACEHOLDER_VALUES
    }


def _default_artifact_tool_node_modules() -> Path:
    return (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "node_modules"
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = value.split(".")[:3]
    normalized: list[int] = []
    for part in parts:
        digits = "".join(char for char in part if char.isdigit())
        normalized.append(int(digits or 0))
    return tuple((normalized + [0, 0, 0])[:3])  # type: ignore[return-value]


def _is_valid_fabric_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.fabric.microsoft.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return False
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 7 or parts[:3] != ["v1", "mcp", "workspaces"]:
        return False
    if parts[4] != "dataagents" or parts[6] != "agent":
        return False
    try:
        UUID(parts[3])
        UUID(parts[5])
    except ValueError:
        return False
    return True
