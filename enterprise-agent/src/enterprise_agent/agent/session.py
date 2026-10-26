"""Concurrency-safe in-memory Agent Framework sessions for development."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_framework import AgentSession

from enterprise_agent.storage.database import Database


@dataclass(slots=True)
class SessionEntry:
    session: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_save: asyncio.Task[None] | None = field(default=None, repr=False)

    async def wait_for_save(self, *, propagate_error: bool = True) -> None:
        """A cancelled request must not cancel or race a database commit in a thread."""
        pending = self.pending_save
        if pending is not None:
            try:
                await asyncio.shield(pending)
            except Exception:
                if propagate_error:
                    raise
            finally:
                if pending.done() and self.pending_save is pending:
                    self.pending_save = None


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
        entry = self._sessions.get(session_id)
        if entry is None:
            return False
        async with entry.lock:
            await entry.wait_for_save(propagate_error=False)
            async with self._store_lock:
                if self._sessions.get(session_id) is not entry:
                    return False
                del self._sessions[session_id]
                return True

    @asynccontextmanager
    async def locked(self, session_id: str) -> AsyncIterator[SessionEntry]:
        while True:
            entry = await self.get_or_create(session_id)
            async with entry.lock:
                # A deletion may have completed while this turn waited on the old lock.
                if self._sessions.get(session_id) is not entry:
                    continue
                await entry.wait_for_save()
                yield entry
                return

    @property
    def count(self) -> int:
        return len(self._sessions)

    async def save(self, session_id: str) -> None:
        """Memory sessions are already updated in place."""


class PersistentSessionStore(InMemorySessionStore):
    """Persist Agent Framework state. Run one worker per local/pilot instance."""

    def __init__(self, database: Database) -> None:
        super().__init__(AgentSession)
        self.database = database
        self.versions: dict[str, int] = {}

    async def get_or_create(self, session_id: str) -> SessionEntry:
        async with self._store_lock:
            if session_id not in self._sessions:
                saved = await asyncio.to_thread(
                    self.database.get, "framework_sessions", session_id, "runtime"
                )
                self.versions[session_id] = saved[1] if saved else 0
                session = (
                    AgentSession.from_dict(saved[0])
                    if saved
                    else AgentSession(session_id=session_id)
                )
                self._sessions[session_id] = SessionEntry(session)
            return self._sessions[session_id]

    async def save(self, session_id: str) -> None:
        entry = self._sessions[session_id]
        await entry.wait_for_save()

        async def persist() -> None:
            version = await asyncio.to_thread(
                self.database.put,
                "framework_sessions",
                session_id,
                "runtime",
                entry.session.to_dict(),
                self.versions[session_id],
            )
            self.versions[session_id] = version

        entry.pending_save = asyncio.create_task(persist())
        # Retrieve background exceptions even if a timed-out caller never returns. The
        # task retains the exception for the next waiter to observe before using state.
        entry.pending_save.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        await entry.wait_for_save()

    async def delete(self, session_id: str) -> bool:
        async def remove() -> bool:
            removed = await asyncio.to_thread(
                self.database.delete, "framework_sessions", session_id, "runtime"
            )
            self._sessions.pop(session_id, None)
            self.versions.pop(session_id, None)
            return removed

        async with self._store_lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return await remove()
        async with entry.lock:
            await entry.wait_for_save(propagate_error=False)
            async with self._store_lock:
                if self._sessions.get(session_id) is not entry:
                    return False
                return await remove()
