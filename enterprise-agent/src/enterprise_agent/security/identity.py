"""Verified request identity shared with agent tools through context-local state."""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    object_id: str
    subject: str
    username: str | None
    scopes: frozenset[str]
    client_id: str | None
    access_token: str = field(repr=False)
    authenticated: bool = True

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls(
            tenant_id="anonymous",
            object_id="anonymous",
            subject="anonymous",
            username=None,
            scopes=frozenset(),
            client_id=None,
            access_token="",
            authenticated=False,
        )

    @property
    def isolation_key(self) -> str:
        return f"{self.tenant_id}:{self.object_id}"


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal

    def session_storage_key(self, public_session_id: str) -> str:
        return f"{self.principal.isolation_key}:{public_session_id}"


_request_context: ContextVar[RequestContext | None] = ContextVar(
    "enterprise_agent_request_context",
    default=None,
)


def set_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _request_context.set(context)


def get_request_context() -> RequestContext:
    context = _request_context.get()
    if context is None:
        raise RuntimeError("No verified request context is available")
    return context


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _request_context.reset(token)

