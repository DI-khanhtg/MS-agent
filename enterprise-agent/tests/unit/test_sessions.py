from dataclasses import dataclass

import pytest

from enterprise_agent.agent.session import InMemorySessionStore


@dataclass
class FakeSession:
    session_id: str


@pytest.mark.asyncio
async def test_session_store_reuses_and_isolates_sessions() -> None:
    store = InMemorySessionStore(lambda *, session_id: FakeSession(session_id))

    first = await store.get_or_create("one")
    repeated = await store.get_or_create("one")
    second = await store.get_or_create("two")

    assert first is repeated
    assert first is not second
    assert store.count == 2


@pytest.mark.asyncio
async def test_session_store_delete() -> None:
    store = InMemorySessionStore(lambda *, session_id: FakeSession(session_id))
    await store.get_or_create("one")

    assert await store.delete("one") is True
    assert await store.delete("one") is False

