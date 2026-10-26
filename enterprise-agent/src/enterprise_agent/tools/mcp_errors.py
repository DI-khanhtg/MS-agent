"""Classify transport failures even when MCP task groups wrap the cause."""

import httpx


def is_retryable_mcp_error(error: BaseException) -> bool:
    return _is_retryable(error, set())


def _is_retryable(error: BaseException, seen: set[int]) -> bool:
    if id(error) in seen:
        return False
    seen = seen | {id(error)}
    if isinstance(error, BaseExceptionGroup):
        # A mixed group containing a permanent failure must not be retried.
        return all(_is_retryable(nested, seen) for nested in error.exceptions)
    if isinstance(
        error, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)
    ):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    else:
        status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    if error.__cause__ is not None:
        return _is_retryable(error.__cause__, seen)
    return False
