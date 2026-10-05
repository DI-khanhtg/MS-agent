"""Chat endpoints, including Server-Sent Events streaming."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from enterprise_agent.agent.runtime import (
    AgentConfigurationError,
    AgentEgressBlocked,
    AgentRequestTimeout,
    AgentRuntime,
    AgentUpstreamError,
)
from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.api.schemas import ChatRequest, ChatResponse
from enterprise_agent.security.identity import RequestContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime


RuntimeDependency = Annotated[AgentRuntime, Depends(get_runtime)]


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        502: {"description": "DeepSeek provider failure"},
        503: {"description": "Missing config"},
    },
)
async def chat(
    payload: ChatRequest,
    runtime: RuntimeDependency,
    principal: PrincipalDependency,
) -> ChatResponse:
    session_id = payload.session_id or str(uuid4())
    try:
        result = await runtime.chat(payload.message, session_id, RequestContext(principal))
    except AgentConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except AgentRequestTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except AgentEgressBlocked as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except AgentUpstreamError as exc:
        logger.error("Upstream model request failed", extra={"session_id": session_id})
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="The model provider could not complete the request.",
        ) from exc
    return ChatResponse(session_id=session_id, message=result.text, tool_calls=result.tool_calls)


@router.post("/chat/stream", response_class=StreamingResponse)
async def stream_chat(
    payload: ChatRequest,
    runtime: RuntimeDependency,
    principal: PrincipalDependency,
) -> StreamingResponse:
    session_id = payload.session_id or str(uuid4())

    async def events() -> AsyncIterator[str]:
        yield _sse("metadata", {"session_id": session_id})
        try:
            async for text in runtime.stream_chat(
                payload.message,
                session_id,
                RequestContext(principal),
            ):
                yield _sse("delta", {"text": text})
            yield _sse("done", {"session_id": session_id})
        except AgentConfigurationError as exc:
            yield _sse("error", {"code": "not_configured", "detail": str(exc)})
        except AgentRequestTimeout:
            yield _sse("error", {"code": "timeout", "detail": "The model request timed out."})
        except AgentEgressBlocked as exc:
            yield _sse("error", {"code": "egress_blocked", "detail": str(exc)})
        except AgentUpstreamError:
            logger.error("Upstream model stream failed", extra={"session_id": session_id})
            yield _sse(
                "error",
                {"code": "upstream_error", "detail": "The model provider request failed."},
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
