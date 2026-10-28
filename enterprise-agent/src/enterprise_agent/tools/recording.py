"""Request-local tool-call recording for API responses and evaluations."""

from contextvars import ContextVar, Token
from typing import Any

_tool_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("tool_calls", default=None)
_tool_budget: ContextVar[int] = ContextVar("tool_budget", default=24)
_last_result: ContextVar[dict | None] = ContextVar("last_stream_result", default=None)


def begin_tool_recording(max_calls: int = 24) -> Token[list[dict[str, Any]] | None]:
    _tool_budget.set(max_calls)
    # Mutate this request-owned holder in place: framework tools can run in child tasks.
    _last_result.set({"tool_calls": [], "usage": {}, "artifacts": []})
    return _tool_calls.set([])


def record_tool_call(name: str, arguments: dict[str, Any]) -> None:
    calls = _tool_calls.get()
    if calls is not None:
        if len(calls) >= _tool_budget.get():
            raise RuntimeError("Tool call budget exceeded; report incomplete coverage")
        calls.append({"name": name, "arguments": arguments})


def get_recorded_tool_calls() -> list[dict[str, Any]]:
    return list(_tool_calls.get() or [])


def end_tool_recording(token: Token[list[dict[str, Any]] | None]) -> None:
    _tool_calls.reset(token)


def set_stream_result(calls: list[dict], usage: dict) -> None:
    result = _last_result.get()
    if result is None:
        result = {"artifacts": []}
        _last_result.set(result)
    result.update(tool_calls=calls, usage=usage)


def get_stream_result() -> dict:
    result = _last_result.get() or {}
    return {"tool_calls": result.get("tool_calls", []), "usage": result.get("usage", {})}


def record_completed_artifact(record: dict[str, Any]) -> None:
    """Record only a successful server-side artifact receipt, never document content."""
    result = _last_result.get()
    if result is not None:
        result["artifacts"].append(dict(record))


def get_completed_artifacts() -> list[dict[str, Any]]:
    result = _last_result.get() or {}
    return [dict(record) for record in result.get("artifacts", [])]
