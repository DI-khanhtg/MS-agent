"""Owner-scoped conversation history, source/tool references, and token accounting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from enterprise_agent.security.identity import Principal, RequestContext
from enterprise_agent.storage.database import Database, StorageConflict


class ConversationStore:
    def __init__(self, database: Database | None, max_messages: int = 40) -> None:
        self.database, self.max_messages = database, max_messages
        self._memory: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()

    def _get(self, session_id: str, principal: Principal) -> tuple[dict, int] | None:
        if self.database:
            key = RequestContext(principal).session_storage_key(session_id)
            saved = self.database.get("conversations", key, principal.isolation_key)
            if saved is not None:
                return saved
            # Migrate records from the first local build without losing demo history.
            legacy = self.database.get("conversations", session_id, principal.isolation_key)
            if legacy is not None:
                try:
                    self.database.put("conversations", key, principal.isolation_key, legacy[0], 0)
                except StorageConflict:
                    pass  # Another request already migrated this owner's record.
                self.database.delete("conversations", session_id, principal.isolation_key)
                return self.database.get("conversations", key, principal.isolation_key)
            return None
        value = self._memory.get((principal.isolation_key, session_id))
        return (value, 0) if value else None

    async def get(self, session_id: str, principal: Principal) -> dict | None:
        saved = await asyncio.to_thread(self._get, session_id, principal)
        return saved[0] if saved else None

    async def list(self, principal: Principal) -> list[dict]:
        if self.database:
            values = await asyncio.to_thread(
                self.database.list, "conversations", principal.isolation_key
            )
        else:
            values = [
                v for (owner, _), v in self._memory.items() if owner == principal.isolation_key
            ]
        return [{k: v for k, v in item.items() if k != "messages"} for item in values]

    async def append(
        self,
        session_id: str,
        principal: Principal,
        message: str,
        answer: str,
        tools: list[dict],
        usage: dict,
    ) -> None:
        async with self._lock:
            saved = await asyncio.to_thread(self._get, session_id, principal)
            item, version = (
                saved if saved else ({"session_id": session_id, "messages": [], "turns": 0}, 0)
            )
            item = {**item, "messages": list(item["messages"])}
            item["messages"].extend(
                [
                    {"role": "user", "text": message},
                    {"role": "assistant", "text": answer, "tool_calls": tools, "usage": usage},
                ]
            )
            item["messages"] = item["messages"][-self.max_messages :]
            item["turns"] += 1
            item["updated_at"] = datetime.now(UTC).isoformat()
            if self.database:
                await asyncio.to_thread(
                    self.database.put,
                    "conversations",
                    RequestContext(principal).session_storage_key(session_id),
                    principal.isolation_key,
                    item,
                    version,
                )
            else:
                self._memory[(principal.isolation_key, session_id)] = item

    async def delete(self, session_id: str, principal: Principal) -> bool:
        if self.database:
            await asyncio.to_thread(self._get, session_id, principal)
            return await asyncio.to_thread(
                self.database.delete,
                "conversations",
                RequestContext(principal).session_storage_key(session_id),
                principal.isolation_key,
            )
        return self._memory.pop((principal.isolation_key, session_id), None) is not None
