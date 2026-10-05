"""Concurrency-safe in-memory Agent Framework sessions for development."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionEntry:
    session: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemorySessionStore:
    """Keep sessions isolated by ID; replace with persistent storage after the MVP."""

    def __init__(self, session_factory: Callable[..., Any]) -> None:
        self._session_factory = session_factory
        self._sessions: dict[str, SessionEntry] = {}
        self._store_lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> SessionEntry:
        entry = self._sessions.get(session_id)
        if entry is not None:
            return entry
        async with self._store_lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionEntry(self._session_factory(session_id=session_id))
                self._sessions[session_id] = entry
            return entry

    async def delete(self, session_id: str) -> bool:
        async with self._store_lock:
            return self._sessions.pop(session_id, None) is not None

    @property
    def count(self) -> int:
        return len(self._sessions)
