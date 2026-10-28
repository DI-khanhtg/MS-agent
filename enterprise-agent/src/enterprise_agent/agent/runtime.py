"""Runtime boundary around Microsoft Agent Framework."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from agent_framework import AgentSession
from openai import APIConnectionError, APITimeoutError

from enterprise_agent.agent.budget import turn_budget
from enterprise_agent.agent.factory import create_enterprise_agent, create_gemini_client
from enterprise_agent.agent.session import (
    InMemorySessionStore,
    PersistentSessionStore,
    SessionEntry,
)
from enterprise_agent.artifacts.receipts import missing_artifact_links
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.storage.database import Database
from enterprise_agent.telemetry.operations import (
    CircuitBreaker,
    CircuitOpenError,
    ExecutionTiming,
    identity_hash,
    observe_execution,
)
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
    set_stream_result,
)
from enterprise_agent.tools.registry import ToolRegistry, create_default_registry
from enterprise_agent.tools.workiq.links import protect_shared_links

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AgentConfigurationError(RuntimeError):
    """The runtime is missing required local configuration."""


class AgentUpstreamError(RuntimeError):
    """The model provider failed after the configured retries."""


class AgentEmptyResponseError(AgentUpstreamError):
    """The model ended without an answer; replaying tools may duplicate effects."""


class AgentRequestTimeout(RuntimeError):
    """The model request exceeded its deadline."""


class AgentEgressBlocked(RuntimeError):
    """Input or retrieved data was blocked before reaching the external model."""


@dataclass(slots=True)
class AgentResult:
    text: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int] = field(default_factory=dict)


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
    """Run isolated agents over a shared client with optional durable sessions."""

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry | None = None,
        agent: Any | None = None,
        egress_gate: EgressGate | None = None,
        database: Database | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or create_default_registry()
        self._injected_agent = agent
        self._client: Any | None = None
        self._sessions: InMemorySessionStore | None = None
        self._egress_gate = egress_gate or EgressGate.from_yaml(settings.egress_policy_path)
        self._database = database
        self._circuit = CircuitBreaker(
            settings.circuit_failure_threshold, settings.circuit_reset_seconds
        )

    def _ensure_initialized(self) -> None:
        if self._sessions is None:
            if self._injected_agent is not None:
                self._sessions = InMemorySessionStore(self._injected_agent.create_session)
                return
            if not self.settings.gemini_is_configured:
                raise AgentConfigurationError(
                    "Gemini is not configured. Set GEMINI_API_KEY in enterprise-agent/.env."
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
            if "foundry_read" in self.settings.tool_groups and (
                not self.settings.auth_enabled or not self.settings.foundry_iq_is_configured
            ):
                raise AgentConfigurationError("foundry_read requires auth and a configured KB")
            if (
                "local_read" in self.settings.tool_groups
                and not self.settings.local_document_access_enabled
            ):
                raise AgentConfigurationError("local_read is available only in development")
            try:
                self._client = create_gemini_client(self.settings)
            except (ValueError, ImportError) as exc:
                raise AgentConfigurationError(str(exc)) from exc
            self._sessions = (
                PersistentSessionStore(self._database)
                if self._database
                else InMemorySessionStore(AgentSession)
            )

    def _new_agent(self) -> Any:
        self._ensure_initialized()
        if self._injected_agent is not None:
            return self._injected_agent
        try:
            return create_enterprise_agent(self.settings, self.registry, client=self._client)
        except ValueError as exc:
            raise AgentConfigurationError(str(exc)) from exc

    async def _with_retries(
        self,
        operation: Callable[[], Awaitable[T]],
        timing: ExecutionTiming,
    ) -> T:
        attempts = self.settings.gemini_max_retries + 1
        for attempt in range(attempts):
            try:
                timing.attempts += 1
                result = await operation()
                self._circuit.success()
                return result
            except AgentEmptyResponseError:
                self._circuit.failure()
                raise
            except (TimeoutError, APITimeoutError) as exc:
                if get_recorded_tool_calls() or attempt == attempts - 1:
                    self._circuit.failure()
                    raise AgentRequestTimeout("Gemini request timed out") from exc
            except Exception as exc:
                if get_recorded_tool_calls() or not _is_retryable(exc) or attempt == attempts - 1:
                    self._circuit.failure()
                    raise AgentUpstreamError("Gemini request failed") from exc
            delay = min(0.25 * (2**attempt), 2.0)
            logger.warning("Retrying Gemini request", extra={"attempt": attempt + 2})
            await asyncio.sleep(delay)
        raise AssertionError("retry loop exited unexpectedly")

    @asynccontextmanager
    async def _turn(
        self,
        session_id: str,
        context: RequestContext,
        *,
        stream: bool = False,
    ) -> AsyncIterator[tuple[Any, SessionEntry, ExecutionTiming]]:
        request_context = context
        context_token = set_request_context(request_context)
        token = begin_tool_recording(self.settings.max_tool_calls)
        with observe_execution(
            "agent_turn",
            "stream" if stream else "chat",
            mode=self.settings.runtime_mode,
            subject_hash=identity_hash(request_context.principal.isolation_key),
            session_hash=identity_hash(request_context.session_storage_key(session_id)),
        ) as timing:
            try:
                with turn_budget(
                    self.settings.agent_turn_timeout_seconds,
                    self.settings.agent_synthesis_reserve_seconds,
                ):
                    async with asyncio.timeout(self.settings.agent_turn_timeout_seconds):
                        agent = self._new_agent()
                        assert self._sessions is not None
                        async with self._sessions.locked(
                            request_context.session_storage_key(session_id)
                        ) as entry:
                            try:
                                with self._circuit.request():
                                    yield agent, entry, timing
                            except CircuitOpenError as exc:
                                raise AgentUpstreamError(str(exc)) from exc
            except (TimeoutError, AgentRequestTimeout) as exc:
                timing.status = "timeout"
                if isinstance(exc, AgentRequestTimeout):
                    raise
                raise AgentRequestTimeout(
                    "Agent turn timed out; requested work may be incomplete."
                ) from exc
            finally:
                end_tool_recording(token)
                reset_request_context(context_token)

    async def chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AgentResult:
        request_context = context or RequestContext(Principal.anonymous())
        async with self._turn(session_id, request_context) as (agent, entry, timing):
            safe_message = self._sanitize_input(message, request_context.principal)

            async def run():
                response = await agent.run(safe_message, session=entry.session)
                timing.finish_reason = _finish_reason(response)
                text = getattr(response, "text", None) or ""
                if not text.strip() and not missing_artifact_links():
                    timing.finish_reason = timing.finish_reason or "unknown"
                    raise AgentEmptyResponseError("The model returned no answer")
                return response

            response = await self._with_retries(run, timing)
            assert self._sessions is not None
            await self._sessions.save(request_context.session_storage_key(session_id))
            usage = getattr(response, "usage_details", None) or {}
            text = getattr(response, "text", None) or ""
            return AgentResult(
                text=text + missing_artifact_links(text),
                tool_calls=get_recorded_tool_calls(),
                usage={
                    k: int(usage.get(k, 0)) for k in ("input_token_count", "output_token_count")
                },
            )

    async def stream_chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AsyncIterator[str]:
        request_context = context or RequestContext(Principal.anonymous())
        emitted = False
        async with self._turn(session_id, request_context, stream=True) as (agent, entry, timing):
            safe_message = self._sanitize_input(message, request_context.principal)
            attempts = self.settings.gemini_max_retries + 1
            for attempt in range(attempts):
                try:
                    timing.attempts += 1
                    has_text = False
                    parts = []
                    stream = agent.run(safe_message, session=entry.session, stream=True)
                    async for update in stream:
                        reason = _finish_reason(update)
                        if reason is not None:
                            timing.finish_reason = reason
                        if update.text:
                            timing.mark_first_delta()
                            emitted = True
                            has_text = has_text or bool(update.text.strip())
                            parts.append(update.text)
                            yield update.text
                    usage = {}
                    if hasattr(stream, "get_final_response"):
                        final = await stream.get_final_response()
                        usage = getattr(final, "usage_details", None) or {}
                        reason = _finish_reason(final)
                        if reason is not None:
                            timing.finish_reason = reason
                        final_text = getattr(final, "text", None) or ""
                        if not has_text and final_text.strip():
                            timing.mark_first_delta()
                            emitted = has_text = True
                            parts.append(final_text)
                            yield final_text
                    links = missing_artifact_links("".join(parts))
                    if links:
                        timing.mark_first_delta()
                        emitted = has_text = True
                        yield links
                    if not has_text:
                        timing.finish_reason = timing.finish_reason or "unknown"
                        raise AgentEmptyResponseError("The model returned no answer")
                    self._circuit.success()
                    assert self._sessions is not None
                    await self._sessions.save(request_context.session_storage_key(session_id))
                    set_stream_result(
                        get_recorded_tool_calls(),
                        {
                            k: int(usage.get(k, 0))
                            for k in ("input_token_count", "output_token_count")
                        },
                    )
                    return
                except AgentEmptyResponseError:
                    self._circuit.failure()
                    raise
                except (TimeoutError, APITimeoutError) as exc:
                    if emitted or get_recorded_tool_calls() or attempt == attempts - 1:
                        self._circuit.failure()
                        raise AgentRequestTimeout("Gemini stream timed out") from exc
                except Exception as exc:
                    if (
                        emitted
                        or get_recorded_tool_calls()
                        or not _is_retryable(exc)
                        or attempt == attempts - 1
                    ):
                        self._circuit.failure()
                        raise AgentUpstreamError("Gemini stream failed") from exc
                delay = min(0.25 * (2**attempt), 2.0)
                logger.warning("Retrying Gemini stream", extra={"attempt": attempt + 2})
                await asyncio.sleep(delay)

    def _sanitize_input(self, message: str, principal: Principal) -> str:
        try:
            if "workiq_read" in self.settings.tool_groups:
                # Check the complete original before replacing private sharing URLs.
                # Protect URLs before PII masking can corrupt numeric IDs or share tokens.
                decision = self._egress_gate.evaluate(message)
                if not decision.allowed and decision.reason == "prohibited_classification":
                    self._egress_gate.enforce(message, source="user_input", principal=principal)
                message = protect_shared_links(message)
            result = self._egress_gate.enforce(
                message,
                source="user_input",
                principal=principal,
            )
        except EgressBlockedError as exc:
            raise AgentEgressBlocked(str(exc)) from exc
        return str(result["data"])


def _finish_reason(response: Any) -> str | None:
    """Keep provider diagnostics bounded and exclude arbitrary private strings."""
    reason = getattr(response, "finish_reason", None)
    if reason is None:
        return None
    allowed = {
        "stop",
        "length",
        "tool_calls",
        "function_call",
        "content_filter",
        "malformed_function_call",
        "error",
    }
    return reason.lower() if isinstance(reason, str) and reason.lower() in allowed else "other"


def _is_retryable(exc: Exception) -> bool:
    """Retry timeouts, throttling, and server failures without depending on SDK internals."""
    if isinstance(exc, (ConnectionError, TimeoutError, APIConnectionError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        return True
    cause = getattr(exc, "__cause__", None)
    cause_status = getattr(cause, "status_code", None)
    return cause_status == 429 or (isinstance(cause_status, int) and cause_status >= 500)
