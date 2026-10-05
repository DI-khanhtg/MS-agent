"""Typed application configuration loaded from the local .env file."""

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_VALUES = {"", "PUT_YOUR_DEEPSEEK_API_KEY_HERE", "TO_BE_FILLED_LATER"}


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

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_timeout_seconds: float = Field(default=60, gt=0, le=300)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)

    auth_enabled: bool = False
    azure_tenant_id: str = "TO_BE_FILLED_LATER"
    azure_client_id: str = "TO_BE_FILLED_LATER"
    azure_client_secret: SecretStr = SecretStr("TO_BE_FILLED_LATER")
    entra_api_audience: str = "TO_BE_FILLED_LATER"
    entra_required_scopes: str = "access_as_user"
    auth_allowed_tenant_ids: str = "TO_BE_FILLED_LATER"
    entra_allowed_client_ids: str = "TO_BE_FILLED_LATER"

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

    @field_validator("deepseek_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

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
    )
    @classmethod
    def resolve_project_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def deepseek_is_configured(self) -> bool:
        return self.deepseek_api_key.get_secret_value().strip() not in PLACEHOLDER_VALUES

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

    @property
    def required_scopes(self) -> set[str]:
        return _csv_set(self.entra_required_scopes)

    @property
    def allowed_client_ids(self) -> set[str]:
        return _csv_set(self.entra_allowed_client_ids)

    @property
    def tool_groups(self) -> set[str]:
        return _csv_set(self.enabled_tool_groups)

    @property
    def entra_is_configured(self) -> bool:
        return bool(self.allowed_tenant_ids and self.entra_api_audiences)

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
    return Settings()


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
