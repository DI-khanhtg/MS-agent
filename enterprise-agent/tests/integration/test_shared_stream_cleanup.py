"""A disconnected streaming client must release admission and upstream iterators."""

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from starlette.requests import ClientDisconnect, Request

from enterprise_agent.api.chat import stream_chat
from enterprise_agent.api.schemas import ChatRequest
from enterprise_agent.hosting import ChatCapacity
from enterprise_agent.security.identity import Principal


class AllowRequests:
    async def allow(self, owner):
        return True


class StreamingRuntime:
    def __init__(self):
        self.started = False
        self.closed = False
        self.iterator = None

    def stream_chat(self, *args, **kwargs):
        async def generate():
            self.started = True
            try:
                yield "First provider delta"
                await asyncio.Event().wait()
            finally:
                self.closed = True

        self.iterator = generate()
        return self.iterator


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_body", [1, 2], ids=["metadata", "first-delta"])
@pytest.mark.parametrize("failure", ["disconnect", "cancel"])
async def test_shared_stream_closes_admission_and_provider_on_failed_send(failed_body, failure):
    state = SimpleNamespace(
        settings=SimpleNamespace(deployment_mode="shared"),
        shared_chat_limiter=AllowRequests(),
        chat_capacity=ChatCapacity(total=1, per_user=1),
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat/stream",
        "headers": [],
        "app": SimpleNamespace(state=state),
        "asgi": {"spec_version": "2.4"},
    }
    runtime = StreamingRuntime()
    response = await stream_chat(
        ChatRequest(message="hello"), Request(scope), runtime, Principal.anonymous()
    )
    body_count = 0

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal body_count
        if message["type"] == "http.response.body":
            body_count += 1
            if body_count == failed_body:
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise OSError("The streaming client disconnected")

    try:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ClientDisconnect):
            await response(scope, receive, send)
        # Assert synchronously: cleanup cannot depend on garbage collection or shutdown.
        assert state.chat_capacity.active == 0
        assert not state.chat_capacity.users
        assert runtime.started is (failed_body == 2)
        assert runtime.closed is (failed_body == 2)
    finally:
        # Keep a RED run from leaking suspended test iterators into loop shutdown.
        await response.body_iterator.aclose()
        if runtime.iterator is not None:
            await runtime.iterator.aclose()


@pytest.mark.asyncio
async def test_asgi23_disconnect_closes_provider_in_its_original_context():
    provider_context = ContextVar("provider_context", default=None)
    closed = False

    class ContextRuntime:
        async def stream_chat(self, *args, **kwargs):
            nonlocal closed
            token = provider_context.set("provider active")
            try:
                yield "First provider delta"
            finally:
                # The real runtime also resets ContextVar tokens when its turn closes.
                provider_context.reset(token)
                closed = True

    state = SimpleNamespace(
        settings=SimpleNamespace(deployment_mode="shared"),
        shared_chat_limiter=AllowRequests(),
        chat_capacity=ChatCapacity(total=1, per_user=1),
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat/stream",
        "headers": [],
        "app": SimpleNamespace(state=state),
        "asgi": {"spec_version": "2.3"},
    }
    response = await stream_chat(
        ChatRequest(message="hello"), Request(scope), ContextRuntime(), Principal.anonymous()
    )
    delta_send_started = asyncio.Event()
    body_count = 0

    async def receive():
        await delta_send_started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal body_count
        if message["type"] == "http.response.body":
            body_count += 1
            if body_count == 2:
                delta_send_started.set()
                # Disconnect while sending a yielded delta, rather than awaiting a new one.
                await asyncio.Event().wait()

    await asyncio.wait_for(response(scope, receive, send), timeout=5)
    assert state.chat_capacity.active == 0
    assert not state.chat_capacity.users
    assert closed
    assert provider_context.get() is None
