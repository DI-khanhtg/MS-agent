"""Chat endpoints, including Server-Sent Events streaming."""

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from enterprise_agent.agent.runtime import (
    AgentConfigurationError,
    AgentEgressBlocked,
    AgentEmptyResponseError,
    AgentRequestTimeout,
    AgentRuntime,
    AgentUpstreamError,
)
from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.api.schemas import ChatRequest, ChatResponse
from enterprise_agent.artifacts.receipts import missing_artifact_links
from enterprise_agent.artifacts.renderers import ArtifactRenderingError
from enterprise_agent.artifacts.store import ArtifactNotFoundError
from enterprise_agent.hosting import chat_slot, check_chat_rate
from enterprise_agent.security.identity import (
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.telemetry.operations import identity_hash
from enterprise_agent.tools.local.documents import LocalDocumentError
from enterprise_agent.tools.recording import get_completed_artifacts, get_stream_result

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])
EMPTY_RESPONSE_DETAIL = (
    "Agent đã kết thúc nhưng không trả về nội dung. "
    "Yêu cầu có thể chưa hoàn tất; hãy kiểm tra mục tài liệu trước khi thử lại."
)


class ClosingStreamingResponse(StreamingResponse):
    async def stream_response(self, send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # Close in the task that advanced the iterator: runtime ContextVars belong
            # to that task. Shield cleanup from Starlette's disconnect cancellation.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime


RuntimeDependency = Annotated[AgentRuntime, Depends(get_runtime)]


def _runtime_message(payload: ChatRequest, request: Request, principal) -> str:
    """Pass a verified folder selection to the model, keeping visible history readable."""
    if payload.local_folder is None:
        return payload.message
    if "local_read" not in request.app.state.settings.tool_groups:
        raise HTTPException(422, "Local document access is disabled")
    token = set_request_context(RequestContext(principal))
    try:
        folder = request.app.state.local_document_tools.resolve(payload.local_folder)
        if not folder.is_dir():
            raise LocalDocumentError("The selected folder is unavailable for this account")
    except LocalDocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        reset_request_context(token)
    return (
        f"{payload.message}\n\nSelected local document folder (user selection): "
        f"{json.dumps(payload.local_folder, ensure_ascii=False)}. "
        "Use the local read tools for this folder when relevant to the request."
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        502: {"description": "Gemini provider failure"},
        503: {"description": "Missing config"},
    },
)
async def chat(
    payload: ChatRequest,
    request: Request,
    runtime: RuntimeDependency,
    principal: PrincipalDependency,
) -> ChatResponse:
    await check_chat_rate(request)
    async with chat_slot(request, principal.isolation_key) as admitted:
        if not admitted:
            raise HTTPException(
                503,
                "Too many active chat requests. Wait for a turn to finish.",
                headers={"Retry-After": "1"},
            )
        return await _complete_chat(payload, request, runtime, principal)


async def _complete_chat(payload, request, runtime, principal) -> ChatResponse:
    session_id = payload.session_id or str(uuid4())
    try:
        result = await runtime.chat(
            _runtime_message(payload, request, principal), session_id, RequestContext(principal)
        )
        if not result.text.strip():
            raise AgentEmptyResponseError("The runtime returned no answer")
    except AgentConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except AgentRequestTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except AgentEgressBlocked as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except AgentEmptyResponseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=EMPTY_RESPONSE_DETAIL) from exc
    except AgentUpstreamError as exc:
        logger.error("Upstream model request failed", extra={"session_id": session_id})
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="The model provider could not complete the request.",
        ) from exc
    except (LocalDocumentError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except ArtifactRenderingError as exc:
        raise HTTPException(503, str(exc)) from exc
    await _save_turn(
        request,
        principal,
        session_id,
        payload.message,
        result.text,
        result.tool_calls,
        result.usage,
    )
    return ChatResponse(
        session_id=session_id, message=result.text, tool_calls=result.tool_calls, usage=result.usage
    )


@router.post("/chat/stream", response_class=StreamingResponse)
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    runtime: RuntimeDependency,
    principal: PrincipalDependency,
) -> StreamingResponse:
    await check_chat_rate(request)
    session_id = payload.session_id or str(uuid4())
    message = _runtime_message(payload, request, principal)

    async def events() -> AsyncIterator[str]:
        parts = []
        yield _sse("metadata", {"session_id": session_id})
        try:
            try:
                async with aclosing(
                    runtime.stream_chat(message, session_id, RequestContext(principal))
                ) as updates:
                    async for text in updates:
                        parts.append(text)
                        yield _sse("delta", {"text": text})
            except Exception:
                # A completed write remains usable if the provider subsequently fails.
                links = missing_artifact_links("".join(parts))
                if links:
                    yield _sse("delta", {"text": links})
                raise
            if not "".join(parts).strip():
                raise AgentEmptyResponseError("The runtime returned no answer")
            details = get_stream_result()
            await _save_turn(
                request,
                principal,
                session_id,
                payload.message,
                "".join(parts),
                details["tool_calls"],
                details["usage"],
            )
            yield _sse("done", {"session_id": session_id, **details})
        except AgentConfigurationError as exc:
            yield _sse("error", {"code": "not_configured", "detail": str(exc)})
        except AgentRequestTimeout:
            yield _sse("error", {"code": "timeout", "detail": "The model request timed out."})
        except AgentEgressBlocked as exc:
            yield _sse("error", {"code": "egress_blocked", "detail": str(exc)})
        except AgentEmptyResponseError:
            yield _sse("error", {"code": "empty_response", "detail": EMPTY_RESPONSE_DETAIL})
        except AgentUpstreamError:
            logger.error("Upstream model stream failed", extra={"session_id": session_id})
            yield _sse(
                "error",
                {"code": "upstream_error", "detail": "The model provider request failed."},
            )

        except (LocalDocumentError, ValueError, ArtifactRenderingError) as exc:
            yield _sse("error", {"code": "invalid_source", "detail": str(exc)})

    async def admitted_events() -> AsyncIterator[str]:
        async with chat_slot(request, principal.isolation_key) as admitted:
            if not admitted:
                yield _sse(
                    "error",
                    {
                        "code": "server_busy",
                        "detail": "Too many active chat requests. Wait for a turn to finish.",
                    },
                )
                return
            async with aclosing(events()) as stream:
                async for event in stream:
                    yield event

    return ClosingStreamingResponse(
        admitted_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _save_turn(request, principal, session_id, message, answer, tools, usage):
    gate = request.app.state.egress

    def safe(value):
        decision = gate.evaluate(value)
        return decision.payload if decision.allowed else "[Blocked by egress policy]"

    await request.app.state.conversations.append(
        session_id,
        principal,
        str(safe(message)),
        await _safe_answer(request, principal, answer),
        safe(tools) if isinstance(safe(tools), list) else [],
        usage,
    )
    request.app.state.audit.emit(
        "chat_completed",
        request_id=request.state.request_id,
        session_hash=identity_hash(session_id),
        subject_hash=identity_hash(principal.isolation_key),
        model=request.app.state.settings.gemini_model,
        mode=request.app.state.settings.runtime_mode,
        tool_calls=len(tools),
        input_tokens=usage.get("input_token_count", 0),
        output_tokens=usage.get("output_token_count", 0),
        status="ok",
    )


async def _safe_answer(request, principal, answer: str) -> str:
    """Keep verified owned download URLs intact while masking ordinary answer content."""
    gate = request.app.state.egress
    original = gate.evaluate(answer)
    if not original.allowed:
        return "[Blocked by egress policy]"
    protected = answer
    replacements = {}
    verified_urls = {record["download_url"] for record in get_completed_artifacts()}
    pattern = r"/api/artifacts/([0-9a-fA-F-]{36})/download"
    for match in list(re.finditer(pattern, answer))[:50]:
        url = match.group(0)
        if url in replacements.values():
            continue
        if url not in verified_urls:
            try:
                await anyio.to_thread.run_sync(
                    request.app.state.artifact_service.store.get, match.group(1), principal
                )
            except (ArtifactNotFoundError, ValueError, OSError, RuntimeError):
                continue
        placeholder = "ARTIFACTLINK" + uuid4().hex.translate(
            str.maketrans("0123456789", "abcdefghij")
        )
        replacements[placeholder] = url
        protected = protected.replace(url, placeholder)
    decision = gate.evaluate(protected)
    if not decision.allowed:
        return "[Blocked by egress policy]"
    text = str(decision.payload)
    for placeholder, url in replacements.items():
        text = text.replace(placeholder, url)
    return text
