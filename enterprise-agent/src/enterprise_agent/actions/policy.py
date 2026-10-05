"""Server-owned allowlist for controlled actions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from enterprise_agent.actions.models import ActionType, validate_action_payload


class ApprovalPolicyError(ValueError):
    """An action violates the controlled-action policy."""


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    expiry_minutes: int
    enabled_actions: frozenset[ActionType]
    allowed_update_paths: dict[str, frozenset[str]]

    @classmethod
    def from_yaml(cls, path: Path) -> "ApprovalPolicy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        expiry = int(raw.get("approval_expiry_minutes", 30))
        if not 1 <= expiry <= 1_440:
            raise ApprovalPolicyError("approval expiry must be between 1 and 1440 minutes")
        enabled: set[ActionType] = set()
        for value in raw.get("enabled_actions", []):
            try:
                enabled.add(ActionType(value))
            except ValueError as exc:
                raise ApprovalPolicyError(f"Unknown enabled action: {value}") from exc
        if not enabled:
            raise ApprovalPolicyError("at least one controlled action must be enabled")
        if "delete_entity" in raw.get("blocked_operations", []):
            pass
        else:
            raise ApprovalPolicyError("delete_entity must remain explicitly blocked")
        update_paths = {
            str(prefix).casefold(): frozenset(str(field) for field in fields)
            for prefix, fields in (raw.get("allowed_update_paths", {}) or {}).items()
        }
        return cls(expiry, frozenset(enabled), update_paths)

    def validate(self, action_type: ActionType, payload: dict[str, Any]) -> dict[str, Any]:
        if action_type not in self.enabled_actions:
            raise ApprovalPolicyError(f"Action is not enabled: {action_type.value}")
        validated = validate_action_payload(action_type, payload)
        if action_type is ActionType.UPDATE_ENTITY:
            self._validate_update(validated)
        return validated

    def _validate_update(self, payload: dict[str, Any]) -> None:
        path = _validate_relative_path(str(payload["entity_url"]))
        matched_fields: frozenset[str] | None = None
        for prefix, fields in self.allowed_update_paths.items():
            if path.casefold().startswith(prefix):
                matched_fields = fields
                break
        if matched_fields is None:
            raise ApprovalPolicyError("Entity path is not enabled for update")
        requested_fields = set(payload["changes"])
        if not requested_fields <= matched_fields:
            blocked = ", ".join(sorted(requested_fields - matched_fields))
            raise ApprovalPolicyError(f"Entity update contains blocked fields: {blocked}")


def _validate_relative_path(value: str) -> str:
    if not value.startswith("/"):
        raise ApprovalPolicyError("Entity URL must be a relative path")
    parsed = urlsplit(value)
    decoded = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ApprovalPolicyError("Entity URL cannot contain host, query, or fragment")
    if ".." in decoded.split("/") or "/authentication/" in decoded.casefold():
        raise ApprovalPolicyError("Entity URL contains a blocked path segment")
    return decoded

