"""Delegated Work IQ token acquisition through the OAuth on-behalf-of flow."""

import asyncio
from typing import Any, Protocol

import msal

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal


class WorkIQAuthenticationError(RuntimeError):
    """A delegated Work IQ access token could not be acquired."""


class WorkIQTokenProvider(Protocol):
    async def get_token(self, principal: Principal) -> str: ...


class MsalOnBehalfOfTokenProvider:
    """Exchange a verified incoming user token for a Work IQ delegated token."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._applications: dict[str, msal.ConfidentialClientApplication] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_token(self, principal: Principal) -> str:
        if not principal.authenticated or not principal.access_token:
            raise WorkIQAuthenticationError("Work IQ requires an authenticated user")
        if principal.tenant_id not in self.settings.allowed_tenant_ids:
            raise WorkIQAuthenticationError("The user's tenant is not allowed")
        if not self.settings.workiq_obo_is_configured:
            raise WorkIQAuthenticationError("Entra OBO credentials are not configured")

        lock = self._locks.setdefault(principal.tenant_id, asyncio.Lock())
        async with lock:
            application = self._applications.get(principal.tenant_id)
            if application is None:
                application = msal.ConfidentialClientApplication(
                    client_id=self.settings.azure_client_id,
                    client_credential=self.settings.azure_client_secret.get_secret_value(),
                    authority=(
                        "https://login.microsoftonline.com/"
                        f"{principal.tenant_id}"
                    ),
                )
                self._applications[principal.tenant_id] = application
            result: dict[str, Any] = await asyncio.to_thread(
                application.acquire_token_on_behalf_of,
                user_assertion=principal.access_token,
                scopes=[self.settings.workiq_scope],
            )
        access_token = result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            error = result.get("error", "token_exchange_failed")
            correlation_id = result.get("correlation_id", "unavailable")
            raise WorkIQAuthenticationError(
                f"Work IQ token exchange failed ({error}; correlation_id={correlation_id})"
            )
        return access_token
