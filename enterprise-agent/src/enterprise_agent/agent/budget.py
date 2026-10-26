"""Request-local deadlines shared by the runtime and enterprise read adapters."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from enterprise_agent.security.identity import get_request_context
from enterprise_agent.telemetry.operations import identity_hash, observe_execution

_read_deadline: ContextVar[float | None] = ContextVar("read_deadline", default=None)


@contextmanager
def turn_budget(total_seconds: float, reserve_seconds: float) -> Iterator[None]:
    token = _read_deadline.set(time.monotonic() + total_seconds - reserve_seconds)
    try:
        yield
    finally:
        _read_deadline.reset(token)


def remaining_read_seconds() -> float | None:
    deadline = _read_deadline.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Source read budget exhausted; report incomplete coverage")
    return remaining


@asynccontextmanager
async def read_window(stage: str) -> AsyncIterator[None]:
    """Cap OBO, network retries and extraction together, leaving time for synthesis."""
    with observe_execution(
        "source_read",
        stage,
        subject_hash=identity_hash(get_request_context().principal.isolation_key),
    ):
        async with asyncio.timeout(remaining_read_seconds()):
            yield
