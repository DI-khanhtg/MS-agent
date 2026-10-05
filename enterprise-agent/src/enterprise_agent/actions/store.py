"""Single-process, tenant/user-isolated approval state machine."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from enterprise_agent.actions.models import ActionRecord, ActionStatus, ActionType
from enterprise_agent.security.identity import Principal


class ActionNotFoundError(KeyError):
    pass


class ActionStateError(RuntimeError):
    pass


@dataclass(slots=True)
class _OwnedAction:
    owner_key: str
    record: ActionRecord


class InMemoryApprovalStore:
    """Fail-closed state for one API process; durable storage belongs to Phase 12."""

    def __init__(self, expiry_minutes: int) -> None:
        self.expiry_minutes = expiry_minutes
        self._items: dict[str, _OwnedAction] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        principal: Principal,
        action_type: ActionType,
        payload: dict[str, object],
        summary: str,
    ) -> ActionRecord:
        now = datetime.now(UTC)
        record = ActionRecord(
            action_id=str(uuid4()),
            action_type=action_type,
            status=ActionStatus.DRAFT,
            summary=summary,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._items[record.action_id] = _OwnedAction(_owner_key(principal), record)
        return record

    async def get(self, action_id: str, principal: Principal) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            self._expire_if_needed(owned)
            return owned.record

    async def list(self, principal: Principal) -> list[ActionRecord]:
        owner_key = _owner_key(principal)
        async with self._lock:
            records = []
            for owned in self._items.values():
                if owned.owner_key == owner_key:
                    self._expire_if_needed(owned)
                    records.append(owned.record)
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    async def request_approval(self, action_id: str, principal: Principal) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            if owned.record.status is not ActionStatus.DRAFT:
                raise ActionStateError("Only a draft can be submitted for approval")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.PENDING_APPROVAL,
                    "updated_at": now,
                    "expires_at": now + timedelta(minutes=self.expiry_minutes),
                }
            )
            return owned.record

    async def approve(self, action_id: str, principal: Principal) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            self._expire_if_needed(owned)
            if owned.record.status is not ActionStatus.PENDING_APPROVAL:
                raise ActionStateError("Only a pending action can be approved")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.APPROVED,
                    "approved_at": now,
                    "updated_at": now,
                }
            )
            return owned.record

    async def reject(self, action_id: str, principal: Principal, reason: str) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            self._expire_if_needed(owned)
            if owned.record.status is not ActionStatus.PENDING_APPROVAL:
                raise ActionStateError("Only a pending action can be rejected")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.REJECTED,
                    "rejection_reason": reason,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            return owned.record

    async def claim_execution(self, action_id: str, principal: Principal) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            if owned.record.status is not ActionStatus.APPROVED:
                raise ActionStateError("Action has not been approved for execution")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={"status": ActionStatus.EXECUTING, "updated_at": now}
            )
            return owned.record

    async def complete(
        self,
        action_id: str,
        principal: Principal,
        *,
        status_code: int,
        resource_id: str | None,
        verification: str,
    ) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            if owned.record.status is not ActionStatus.EXECUTING:
                raise ActionStateError("Action is not executing")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.SUCCEEDED,
                    "provider_status_code": status_code,
                    "provider_resource_id": resource_id,
                    "verification": verification,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            return owned.record

    async def fail(
        self,
        action_id: str,
        principal: Principal,
        *,
        error_code: str,
    ) -> ActionRecord:
        async with self._lock:
            owned = self._owned(action_id, principal)
            if owned.record.status is not ActionStatus.EXECUTING:
                raise ActionStateError("Action is not executing")
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.FAILED,
                    "error_code": error_code,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            return owned.record

    def _owned(self, action_id: str, principal: Principal) -> _OwnedAction:
        try:
            normalized = str(UUID(action_id))
            owned = self._items[normalized]
        except (ValueError, KeyError) as exc:
            raise ActionNotFoundError(action_id) from exc
        if owned.owner_key != _owner_key(principal):
            raise ActionNotFoundError(action_id)
        return owned

    @staticmethod
    def _expire_if_needed(owned: _OwnedAction) -> None:
        expires_at = owned.record.expires_at
        if (
            owned.record.status is ActionStatus.PENDING_APPROVAL
            and expires_at is not None
            and expires_at <= datetime.now(UTC)
        ):
            now = datetime.now(UTC)
            owned.record = owned.record.model_copy(
                update={
                    "status": ActionStatus.EXPIRED,
                    "updated_at": now,
                    "completed_at": now,
                }
            )


def _owner_key(principal: Principal) -> str:
    return hashlib.sha256(principal.isolation_key.encode("utf-8")).hexdigest()

