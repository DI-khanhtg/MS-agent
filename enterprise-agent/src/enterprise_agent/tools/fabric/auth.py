"""Delegated Microsoft Fabric token acquisition through OAuth OBO."""

import asyncio
from collections import OrderedDict
from typing import Any, Protocol

import msal

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal


class FabricAuthenticationError(RuntimeError):
    """A delegated Fabric access token could not be acquired."""


class FabricTokenProvider(Protocol):
    async def get_token(self, principal: Principal) -> str: ...


class MsalFabricOnBehalfOfTokenProvider:
    """Exchange the verified API token for a user-scoped Fabric token."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._applications: OrderedDict[str, msal.ConfidentialClientApplication] = OrderedDict()
        # A fixed lock pool bounds synchronization state as organizations come and go.
        self._locks = tuple(asyncio.Lock() for _ in range(64))

    async def get_token(self, principal: Principal) -> str:
        if not principal.authenticated or not principal.access_token:
            raise FabricAuthenticationError("Fabric requires an authenticated user")
        if not self.settings.tenant_is_allowed(principal.tenant_id):
            raise FabricAuthenticationError("The user's tenant is not allowed")
        if not self.settings.fabric_obo_is_configured:
            raise FabricAuthenticationError("Fabric OBO credentials are not configured")

        lock = self._locks[hash(principal.tenant_id) % len(self._locks)]
        async with lock:
            application = self._applications.get(principal.tenant_id)
            if application is None:
                application = msal.ConfidentialClientApplication(
                    client_id=self.settings.azure_client_id,
                    client_credential=self.settings.azure_client_secret.get_secret_value(),
                    authority=f"https://login.microsoftonline.com/{principal.tenant_id}",
                )
                self._applications[principal.tenant_id] = application
                while len(self._applications) > 512:
                    self._applications.popitem(last=False)
            else:
                self._applications.move_to_end(principal.tenant_id)
            result: dict[str, Any] = await asyncio.to_thread(
                application.acquire_token_on_behalf_of,
                user_assertion=principal.access_token,
                scopes=[self.settings.fabric_scope],
            )
        access_token = result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            error = result.get("error", "token_exchange_failed")
            correlation_id = result.get("correlation_id", "unavailable")
            raise FabricAuthenticationError(
                f"Fabric token exchange failed ({error}; correlation_id={correlation_id})"
            )
        return access_token
