"""Rate limits, circuit breakers, and structured audit events without raw content."""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, threshold: int = 5, reset_seconds: float = 30) -> None:
        self.threshold, self.reset_seconds = threshold, reset_seconds
        self.failures = 0
        self.open_until = 0.0
        self._probe: object | None = None

    def check(self) -> object | None:
        if self.open_until:
            if time.monotonic() < self.open_until or self._probe:
                raise CircuitOpenError("Upstream temporarily unavailable; retry later")
            self._probe = object()
            return self._probe
        return None

    @contextmanager
    def request(self) -> Iterator[None]:
        """Release only this request's recovery probe, including on cancellation."""
        probe = self.check()
        try:
            yield
        finally:
            if probe is not None and self._probe is probe:
                self._probe = None

    def success(self) -> None:
        self.failures, self.open_until, self._probe = 0, 0.0, None

    def failure(self) -> None:
        self._probe = None
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = time.monotonic() + self.reset_seconds


class RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._entries: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            # Bound expired identity buckets as well as each user's request history.
            if len(self._entries) > 1000:
                self._entries = defaultdict(
                    deque, {k: v for k, v in self._entries.items() if v and v[-1] > now - 60}
                )
            values = self._entries[key]
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.totals: dict[str, float] = defaultdict(float)

    def emit(self, event: str, **metadata: Any) -> None:
        allowed = {
            "request_id",
            "session_hash",
            "subject_hash",
            "status",
            "latency_ms",
            "model",
            "input_tokens",
            "output_tokens",
            "tool_calls",
            "mode",
            "allowed",
            "classifications",
            "reason",
            "redaction_count",
            "truncated",
            "original_bytes",
            "output_bytes",
            "action_id",
            "action_type",
            "artifact_id",
            "stage",
            "attempts",
            "first_delta_ms",
        }
        safe = {k: v for k, v in metadata.items() if k in allowed}
        record = {"time": time.time(), "event": event, **safe}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.totals["events"] += 1
            if event == "http_request":
                self.totals["requests"] += 1
            for key in ("input_tokens", "output_tokens", "tool_calls"):
                self.totals[key] += float(safe.get(key, 0))
        logging.getLogger(__name__).info("agent_event", extra={"audit": record})


def identity_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


audit_sink: ContextVar[AuditLog | None] = ContextVar("audit_sink", default=None)


def audit_event(event: str, **metadata) -> None:
    sink = audit_sink.get()
    if sink is not None:
        sink.emit(event, **metadata)


@dataclass(slots=True)
class ExecutionTiming:
    started: float = field(default_factory=time.monotonic)
    status: str = "ok"
    attempts: int = 0
    first_delta_ms: float | None = None

    def mark_first_delta(self) -> None:
        if self.first_delta_ms is None:
            self.first_delta_ms = (time.monotonic() - self.started) * 1000


@contextmanager
def observe_execution(
    event: str,
    stage: str,
    *,
    mode: str | None = None,
    subject_hash: str | None = None,
    session_hash: str | None = None,
) -> Iterator[ExecutionTiming]:
    """Record durations and outcomes only; no arguments, results or exception messages."""
    timing = ExecutionTiming()
    try:
        yield timing
    except BaseException as exc:
        if timing.status == "ok":
            timing.status = (
                "cancelled"
                if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                else "timeout"
                if isinstance(exc, TimeoutError)
                else "error"
            )
        raise
    finally:
        metadata: dict[str, Any] = {
            "stage": stage,
            "status": timing.status,
            "latency_ms": (time.monotonic() - timing.started) * 1000,
        }
        if event == "agent_turn":
            metadata["attempts"] = timing.attempts
        if timing.first_delta_ms is not None:
            metadata["first_delta_ms"] = timing.first_delta_ms
        metadata.update(
            {
                key: value
                for key, value in (
                    ("mode", mode),
                    ("subject_hash", subject_hash),
                    ("session_hash", session_hash),
                )
                if value is not None
            }
        )
        audit_event(event, **metadata)
