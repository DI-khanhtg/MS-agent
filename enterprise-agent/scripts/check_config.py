"""Print capability readiness without printing secret values or making network calls."""

import json

from enterprise_agent.config import Settings

settings = Settings()
print(
    json.dumps(
        {
            "runtime_mode": settings.runtime_mode,
            "tools": sorted(settings.tool_groups),
            "gemini_ready": settings.gemini_is_configured,
            "workiq_ready": settings.auth_enabled and settings.workiq_obo_is_configured,
            "fabric_ready": settings.auth_enabled and settings.fabric_obo_is_configured,
            "foundry_ready": settings.foundry_iq_is_configured
            and settings.foundry_iq_shared_knowledge,
            "speech_ready": settings.speech_is_configured,
            "storage": settings.storage_backend,
        },
        indent=2,
    )
)
