"""Runtime boundary around Microsoft Agent Framework."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from agent_framework import AgentSession

from enterprise_agent.agent.factory import create_deepseek_client, create_enterprise_agent
from enterprise_agent.agent.session import InMemorySessionStore
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
)
from enterprise_agent.tools.registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AgentConfigurationError(RuntimeError):
    """The runtime is missing required local configuration."""


class AgentUpstreamError(RuntimeError):
    """The model provider failed after the configured retries."""


class AgentRequestTimeout(RuntimeError):
    """The model request exceeded its deadline."""


class AgentEgressBlocked(RuntimeError):
    """Input or retrieved data was blocked before reaching the external model."""


@dataclass(slots=True)
class AgentResult:
    text: str
    tool_calls: list[dict[str, Any]]


class AgentRuntime(Protocol):
    async def chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AgentResult: ...

    def stream_chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AsyncIterator[str]: ...


class MicrosoftAgentRuntime:
    """Run isolated agents over a shared client and maintain in-memory sessions."""

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry | None = None,
        agent: Any | None = None,
        egress_gate: EgressGate | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or create_default_registry()
        self._injected_agent = agent
        self._client: Any | None = None
        self._sessions: InMemorySessionStore | None = None
        self._egress_gate = egress_gate or EgressGate.from_yaml(settings.egress_policy_path)

    def _ensure_initialized(self) -> None:
        if self._sessions is None:
            if self._injected_agent is not None:
                self._sessions = InMemorySessionStore(self._injected_agent.create_session)
                return
            if not self.settings.deepseek_is_configured:
                raise AgentConfigurationError(
                    "DeepSeek is not configured. Set DEEPSEEK_API_KEY in enterprise-agent/.env."
                )
            if "workiq_read" in self.settings.tool_groups and (
                not self.settings.auth_enabled or not self.settings.workiq_obo_is_configured
            ):
                raise AgentConfigurationError(
                    "workiq_read requires enabled and fully configured Entra authentication"
                )
            if "fabric_read" in self.settings.tool_groups and (
                not self.settings.auth_enabled or not self.settings.fabric_obo_is_configured
            ):
                raise AgentConfigurationError(
                    "fabric_read requires enabled and fully configured Fabric OBO authentication"
                )
            if "workiq_write" in self.settings.tool_groups and (
                not self.settings.auth_enabled or not self.settings.workiq_obo_is_configured
            ):
                raise AgentConfigurationError(
                    "workiq_write requires enabled and fully configured Work IQ OBO authentication"
                )
            if (
                "workiq_write" in self.settings.tool_groups
                and not self.settings.require_write_approval
            ):
                raise AgentConfigurationError(
                    "workiq_write cannot start when backend approval enforcement is disabled"
                )
            try:
                self._client = create_deepseek_client(self.settings)
            except (ValueError, ImportError) as exc:
                raise AgentConfigurationError(str(exc)) from exc
            self._sessions = InMemorySessionStore(AgentSession)

    def _new_agent(self) -> Any:
        self._ensure_initialized()
        if self._injected_agent is not None:
            return self._injected_agent
        try:
            return create_enterprise_agent(self.settings, self.registry, client=self._client)
        except ValueError as exc:
            raise AgentConfigurationError(str(exc)) from exc

    async def _with_retries(self, operation: Callable[[], Awaitable[T]]) -> T:
        attempts = self.settings.deepseek_max_retries + 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(self.settings.deepseek_timeout_seconds):
                    return await operation()
            except TimeoutError as exc:
                if attempt == attempts - 1:
                    raise AgentRequestTimeout("DeepSeek request timed out") from exc
            except Exception as exc:
                if not _is_retryable(exc) or attempt == attempts - 1:
                    raise AgentUpstreamError("DeepSeek request failed") from exc
            delay = min(0.25 * (2**attempt), 2.0)
            logger.warning("Retrying DeepSeek request", extra={"attempt": attempt + 2})
            await asyncio.sleep(delay)
        raise AssertionError("retry loop exited unexpectedly")

    async def chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AgentResult:
        agent = self._new_agent()
        assert self._sessions is not None
        request_context = context or RequestContext(Principal.anonymous())
        safe_message = self._sanitize_input(message, request_context.principal)
        entry = await self._sessions.get_or_create(
            request_context.session_storage_key(session_id)
        )
        context_token = set_request_context(request_context)
        token = begin_tool_recording()
        try:
            async with entry.lock:
                response = await self._with_retries(
                    lambda: agent.run(safe_message, session=entry.session)
                )
            return AgentResult(text=response.text, tool_calls=get_recorded_tool_calls())
        finally:
            end_tool_recording(token)
            reset_request_context(context_token)

    async def stream_chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AsyncIterator[str]:
        agent = self._new_agent()
        assert self._sessions is not None
        request_context = context or RequestContext(Principal.anonymous())
        safe_message = self._sanitize_input(message, request_context.principal)
        entry = await self._sessions.get_or_create(
            request_context.session_storage_key(session_id)
        )
        context_token = set_request_context(request_context)
        token = begin_tool_recording()
        emitted = False
        try:
            async with entry.lock:
                attempts = self.settings.deepseek_max_retries + 1
                for attempt in range(attempts):
                    try:
                        async with asyncio.timeout(self.settings.deepseek_timeout_seconds):
                            stream = agent.run(safe_message, session=entry.session, stream=True)
                            async for update in stream:
                                if update.text:
                                    emitted = True
                                    yield update.text
                        return
                    except TimeoutError as exc:
                        if emitted or attempt == attempts - 1:
                            raise AgentRequestTimeout("DeepSeek stream timed out") from exc
                    except Exception as exc:
                        if emitted or not _is_retryable(exc) or attempt == attempts - 1:
                            raise AgentUpstreamError("DeepSeek stream failed") from exc
                    delay = min(0.25 * (2**attempt), 2.0)
                    logger.warning("Retrying DeepSeek stream", extra={"attempt": attempt + 2})
                    await asyncio.sleep(delay)
        finally:
            end_tool_recording(token)
            reset_request_context(context_token)

    def _sanitize_input(self, message: str, principal: Principal) -> str:
        try:
            result = self._egress_gate.enforce(
                message,
                source="user_input",
                principal=principal,
            )
        except EgressBlockedError as exc:
            raise AgentEgressBlocked(str(exc)) from exc
        return str(result["data"])


def _is_retryable(exc: Exception) -> bool:
    """Retry timeouts, throttling, and server failures without depending on SDK internals."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        return True
    cause = getattr(exc, "__cause__", None)
    cause_status = getattr(cause, "status_code", None)
    return cause_status == 429 or (isinstance(cause_status, int) and cause_status >= 500)
