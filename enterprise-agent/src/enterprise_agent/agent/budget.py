"""Request-local deadlines shared by the runtime and enterprise read adapters."""

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar
from uuid import uuid4

from enterprise_agent.security.identity import get_request_context
from enterprise_agent.telemetry.operations import identity_hash, observe_execution

_read_deadline: ContextVar[float | None] = ContextVar("read_deadline", default=None)
_read_cache: ContextVar["_TurnCache | None"] = ContextVar("read_cache", default=None)
T = TypeVar("T")


# Large folder listings register one handle per entry shown to the model.
_MAX_REFERENCES = 400


class _TurnCache:
    """Small RAM-only cache; never kept on the shared tool/client instance."""

    def __init__(self) -> None:
        self.values: OrderedDict[tuple, Any] = OrderedDict()
        self.locks: dict[tuple, asyncio.Lock] = {}
        self.counts: dict[tuple, int] = {}
        self.references: OrderedDict[tuple, tuple[str, Any]] = OrderedDict()
        self.closed = False

    def close(self) -> None:
        # Copied child-task contexts may retain this object after a cancelled turn.
        self.closed = True
        self.values.clear()
        self.references.clear()
        self.locks.clear()
        self.counts.clear()

    async def load(self, key: tuple, loader: Callable[[], Awaitable[T]]) -> T:
        if self.closed:
            raise TimeoutError("The source-read turn has ended")
        async with self.locks.setdefault(key, asyncio.Lock()):
            if self.closed:
                raise TimeoutError("The source-read turn has ended")
            if key not in self.values:
                value = await loader()  # Exceptions/cancellations are never cached.
                if self.closed:
                    raise TimeoutError("The source-read turn has ended")
                self.values[key] = value
                if len(self.values) > 8:
                    self.values.popitem(last=False)
            self.values.move_to_end(key)
            return self.values[key]


async def cached_source_read(key: tuple, loader: Callable[[], Awaitable[T]]) -> T:
    cache = _read_cache.get()
    if cache is None:
        return await loader()
    return await cache.load(_owner_key(key), loader)


def reserve_source_call(key: tuple, maximum: int) -> bool:
    """Bound potentially billed operations independently of the model's tool-call budget."""
    cache = _read_cache.get()
    if cache is None:
        return True
    if cache.closed:
        return False
    key = _owner_key(key)
    used = cache.counts.get(key, 0)
    if used >= maximum:
        return False
    cache.counts[key] = used + 1
    return True


def register_source_reference(namespace: str, key: tuple, value: Any) -> str | None:
    cache = _read_cache.get()
    if cache is None or cache.closed:
        return None
    key = _owner_key((namespace, *key))
    if key not in cache.references:
        # A random 128-bit identifier encoded as letters avoids PII redaction of IDs.
        opaque = "wiq_" + uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
        cache.references[key] = (opaque, value)
        if len(cache.references) > _MAX_REFERENCES:
            cache.references.popitem(last=False)
    cache.references.move_to_end(key)
    return cache.references[key][0]


def resolve_source_reference(namespace: str, reference: str) -> Any | None:
    cache = _read_cache.get()
    if cache is None or cache.closed:
        return None
    prefix = _owner_key((namespace,))
    for key, (opaque, value) in cache.references.items():
        if key[: len(prefix)] == prefix and opaque == reference:
            return value
    return None


def _owner_key(key: tuple) -> tuple:
    principal = get_request_context().principal
    assertion = hashlib.sha256(principal.access_token.encode()).hexdigest()
    return (principal.isolation_key, assertion, *key)


@contextmanager
def turn_budget(total_seconds: float, reserve_seconds: float) -> Iterator[None]:
    token = _read_deadline.set(time.monotonic() + total_seconds - reserve_seconds)
    cache = _TurnCache()
    cache_token = _read_cache.set(cache)
    try:
        yield
    finally:
        cache.close()
        _read_cache.reset(cache_token)
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
