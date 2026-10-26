"""Persist approval transitions with compare-and-swap before external execution."""

import asyncio

from enterprise_agent.actions.models import ActionRecord
from enterprise_agent.actions.store import (
    ActionNotFoundError,
    ActionStateError,
    InMemoryApprovalStore,
    _OwnedAction,
    _owner_key,
)
from enterprise_agent.storage.database import Database, StorageConflict


class DurableApprovalStore(InMemoryApprovalStore):
    def __init__(self, expiry_minutes: int, database: Database) -> None:
        super().__init__(expiry_minutes)
        self.database = database
        self._durable_lock = asyncio.Lock()

    async def create(self, principal, action_type, payload, summary):
        async with self._durable_lock:
            record = await super().create(principal, action_type, payload, summary)
            await asyncio.to_thread(
                self.database.put,
                "actions",
                record.action_id,
                principal.isolation_key,
                record.model_dump(mode="json"),
                0,
            )
            self._items.pop(record.action_id, None)
            return record

    async def _apply(self, method, action_id, principal, *args, **kwargs):
        async with self._durable_lock:
            saved = await asyncio.to_thread(
                self.database.get, "actions", action_id, principal.isolation_key
            )
            if not saved:
                raise ActionNotFoundError(action_id)
            payload, version = saved
            self._items[action_id] = _OwnedAction(
                _owner_key(principal), ActionRecord.model_validate(payload)
            )
            try:
                record = await getattr(super(), method)(action_id, principal, *args, **kwargs)
                updated = record.model_dump(mode="json")
                if updated != payload:
                    await asyncio.to_thread(
                        self.database.put,
                        "actions",
                        action_id,
                        principal.isolation_key,
                        updated,
                        version,
                    )
                return record
            except StorageConflict as exc:
                raise ActionStateError("Action changed concurrently; refresh its status") from exc
            finally:
                self._items.pop(action_id, None)

    async def get(self, action_id, principal):
        return await self._apply("get", action_id, principal)

    async def list(self, principal):
        items = await asyncio.to_thread(self.database.list, "actions", principal.isolation_key)
        return [await self.get(item["action_id"], principal) for item in items]

    async def request_approval(self, action_id, principal):
        return await self._apply("request_approval", action_id, principal)

    async def approve(self, action_id, principal):
        return await self._apply("approve", action_id, principal)

    async def reject(self, action_id, principal, reason):
        return await self._apply("reject", action_id, principal, reason)

    async def claim_execution(self, action_id, principal):
        return await self._apply("claim_execution", action_id, principal)

    async def complete(self, action_id, principal, **kwargs):
        return await self._apply("complete", action_id, principal, **kwargs)

    async def fail(self, action_id, principal, **kwargs):
        return await self._apply("fail", action_id, principal, **kwargs)
