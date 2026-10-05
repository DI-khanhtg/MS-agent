"""Request-local tool-call recording for API responses and evaluations."""

from contextvars import ContextVar, Token
from typing import Any

_tool_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("tool_calls", default=None)


def begin_tool_recording() -> Token[list[dict[str, Any]] | None]:
    return _tool_calls.set([])


def record_tool_call(name: str, arguments: dict[str, Any]) -> None:
    calls = _tool_calls.get()
    if calls is not None:
        calls.append({"name": name, "arguments": arguments})


def get_recorded_tool_calls() -> list[dict[str, Any]]:
    return list(_tool_calls.get() or [])


def end_tool_recording(token: Token[list[dict[str, Any]] | None]) -> None:
    _tool_calls.reset(token)

