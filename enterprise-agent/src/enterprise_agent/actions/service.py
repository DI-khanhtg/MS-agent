"""Approval orchestration with backend-only execution."""

import hashlib
import logging

from enterprise_agent.actions.executor import ActionExecutionError, WorkIQActionExecutor
from enterprise_agent.actions.models import (
    ActionRecord,
    ActionType,
    summarize_action,
)
from enterprise_agent.actions.policy import ApprovalPolicy
from enterprise_agent.actions.store import InMemoryApprovalStore
from enterprise_agent.security.identity import Principal

logger = logging.getLogger(__name__)


class ApprovalService:
    def __init__(
        self,
        store: InMemoryApprovalStore,
        policy: ApprovalPolicy,
        executor: WorkIQActionExecutor,
    ) -> None:
        self.store = store
        self.policy = policy
        self.executor = executor

    async def draft(
        self,
        principal: Principal,
        action_type: ActionType,
        payload: dict[str, object],
    ) -> ActionRecord:
        validated = self.policy.validate(action_type, payload)
        record = await self.store.create(
            principal,
            action_type,
            validated,
            summarize_action(action_type, validated),
        )
        _audit("action_drafted", record, principal)
        return record

    async def request_approval(self, action_id: str, principal: Principal) -> ActionRecord:
        record = await self.store.request_approval(action_id, principal)
        _audit("approval_requested", record, principal)
        return record

    async def approve_and_execute(self, action_id: str, principal: Principal) -> ActionRecord:
        approved = await self.store.approve(action_id, principal)
        _audit("approval_approved", approved, principal)
        claimed = await self.store.claim_execution(action_id, principal)
        try:
            receipt = await self.executor.execute(claimed, principal)
        except Exception as exc:
            failed = await self.store.fail(action_id, principal, error_code="provider_failure")
            _audit("action_failed", failed, principal)
            if isinstance(exc, ActionExecutionError):
                raise
            raise ActionExecutionError("Approved action execution failed") from exc
        completed = await self.store.complete(
            action_id,
            principal,
            status_code=receipt.status_code,
            resource_id=receipt.resource_id,
            verification=receipt.verification,
        )
        _audit("action_succeeded", completed, principal)
        return completed

    async def reject(
        self,
        action_id: str,
        principal: Principal,
        reason: str,
    ) -> ActionRecord:
        record = await self.store.reject(action_id, principal, reason)
        _audit("approval_rejected", record, principal)
        return record

    async def get(self, action_id: str, principal: Principal) -> ActionRecord:
        return await self.store.get(action_id, principal)

    async def list(self, principal: Principal) -> list[ActionRecord]:
        return await self.store.list(principal)


def _audit(event: str, record: ActionRecord, principal: Principal) -> None:
    logger.info(
        event,
        extra={
            "approval": {
                "action_id": record.action_id,
                "action_type": record.action_type.value,
                "status": record.status.value,
                "subject_hash": hashlib.sha256(
                    principal.isolation_key.encode("utf-8")
                ).hexdigest()[:16],
            }
        },
    )

