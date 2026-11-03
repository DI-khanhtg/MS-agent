"""Explicit, approved retention only; no implicit production retention periods."""

from datetime import datetime, timedelta

from enterprise_agent.config import Settings


class RetentionPolicy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def expiry(self, namespace: str, now: datetime) -> datetime | None:
        if not self.settings.retention_enabled:
            return None
        duration = {
            "conversations": self.settings.conversation_ttl_seconds,
            "framework_sessions": self.settings.session_ttl_seconds,
            "artifacts": self.settings.artifact_ttl_seconds,
            "uploads": self.settings.upload_ttl_seconds,
        }.get(namespace)
        return now + timedelta(seconds=duration) if duration is not None else None
