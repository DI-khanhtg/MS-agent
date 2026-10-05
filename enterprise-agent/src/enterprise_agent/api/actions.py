"""Authenticated draft and approval endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from enterprise_agent.actions.executor import ActionExecutionError
from enterprise_agent.actions.models import ActionRecord, DraftActionRequest, RejectActionRequest
from enterprise_agent.actions.policy import ApprovalPolicyError
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.actions.store import ActionNotFoundError, ActionStateError
from enterprise_agent.api.auth import PrincipalDependency
from enterprise_agent.security.identity import Principal

router = APIRouter(prefix="/api", tags=["controlled-actions"])


def get_approval_service(request: Request) -> ApprovalService:
    return request.app.state.approval_service


ApprovalServiceDependency = Annotated[ApprovalService, Depends(get_approval_service)]


async def require_authenticated_action_principal(principal: PrincipalDependency) -> Principal:
    if not principal.authenticated:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Controlled actions require Microsoft Entra authentication",
        )
    return principal


ActionPrincipalDependency = Annotated[Principal, Depends(require_authenticated_action_principal)]


@router.post("/actions/drafts", response_model=ActionRecord, status_code=status.HTTP_201_CREATED)
async def create_draft(
    payload: DraftActionRequest,
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> ActionRecord:
    try:
        return await service.draft(principal, payload.action_type, payload.payload)
    except ApprovalPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("/actions", response_model=list[ActionRecord])
async def list_actions(
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> list[ActionRecord]:
    return await service.list(principal)


@router.get("/actions/{action_id}", response_model=ActionRecord)
async def get_action(
    action_id: str,
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> ActionRecord:
    try:
        return await service.get(action_id, principal)
    except ActionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Action not found") from None


@router.post("/actions/{action_id}/request-approval", response_model=ActionRecord)
async def request_approval(
    action_id: str,
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> ActionRecord:
    try:
        return await service.request_approval(action_id, principal)
    except ActionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Action not found") from None
    except ActionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/approvals/{action_id}/approve", response_model=ActionRecord)
async def approve_action(
    action_id: str,
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> ActionRecord:
    try:
        return await service.approve_and_execute(action_id, principal)
    except ActionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Action not found") from None
    except ActionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ActionExecutionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/approvals/{action_id}/reject", response_model=ActionRecord)
async def reject_action(
    action_id: str,
    payload: RejectActionRequest,
    service: ApprovalServiceDependency,
    principal: ActionPrincipalDependency,
) -> ActionRecord:
    try:
        return await service.reject(action_id, principal, payload.reason)
    except ActionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Action not found") from None
    except ActionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
