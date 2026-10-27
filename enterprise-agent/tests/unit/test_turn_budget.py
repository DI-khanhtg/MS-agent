import asyncio
import base64
import importlib
import json

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.telemetry.operations import AuditLog, audit_sink
from enterprise_agent.tools.fabric.tools import FabricReadTools
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


def budget_module():
    # Import inside the test so RED reports a missing feature, without hiding other failures.
    return importlib.import_module("enterprise_agent.agent.budget")


@pytest.mark.asyncio
async def test_budget_resets_after_error_and_cancel():
    budget = budget_module()
    with pytest.raises(ValueError), budget.turn_budget(1, 0):
        assert 0 < budget.remaining_read_seconds() <= 1
        raise ValueError("probe")
    assert budget.remaining_read_seconds() is None
    with pytest.raises(asyncio.CancelledError), budget.turn_budget(1, 0):
        raise asyncio.CancelledError()
    assert budget.remaining_read_seconds() is None


@pytest.mark.asyncio
async def test_concurrent_turn_budgets_are_isolated():
    budget = budget_module()
    ready = asyncio.Event()

    async def short_turn():
        with budget.turn_budget(0.02, 0):
            ready.set()
            await asyncio.sleep(0.04)
            with pytest.raises(TimeoutError):
                budget.remaining_read_seconds()

    async def long_turn():
        await ready.wait()
        with budget.turn_budget(1, 0):
            await asyncio.sleep(0.04)
            assert budget.remaining_read_seconds() > 0.5

    await asyncio.gather(short_turn(), long_turn())
    assert budget.remaining_read_seconds() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["workiq", "document", "fabric"])
@pytest.mark.parametrize("phase", ["token", "network", "expired"])
async def test_source_read_window_includes_token_and_network(source, phase, tmp_path):
    budget = budget_module()
    calls = []

    class TokenProvider:
        async def get_token(self, principal):
            calls.append("token")
            if phase == "token":
                await asyncio.sleep(1)
            return "token"

    class Client:
        async def call_tool(self, *args, **kwargs):
            calls.append("network")
            await asyncio.sleep(1)
            return {"structuredContent": {"base64Content": base64.b64encode(b"text").decode()}}

        async def query(self, *args, **kwargs):
            return await self.call_tool()

    settings = Settings(_env_file=None)
    gate = EgressGate.from_yaml(settings.egress_policy_path)
    if source == "fabric":
        tools = FabricReadTools(Client(), TokenProvider(), gate)
    else:
        tools = WorkIQReadTools(Client(), TokenProvider(), gate)

    async def operation():
        if source == "fabric":
            return await tools.fabric_query("Project costs")
        if source == "document":
            return await tools.workiq_read_document("/drives/d/items/i/content", "a.md")
        return await tools.workiq_fetch(["/me/messages?$top=1"])

    context = set_request_context(RequestContext(Principal.anonymous()))
    sink = AuditLog(tmp_path / "audit.jsonl")
    audit_token = audit_sink.set(sink)
    try:
        with budget.turn_budget(1, 0.9):
            if phase == "expired":
                await asyncio.sleep(0.15)
            if source == "document":
                result = await operation()
                assert result["data"]["error"]["code"] == "document_read_timeout"
            else:
                with pytest.raises(TimeoutError):
                    await operation()
    finally:
        audit_sink.reset(audit_token)
        reset_request_context(context)
    assert calls == (
        [] if phase == "expired" else ["token"] if phase == "token" else ["token", "network"]
    )
    events = [json.loads(line) for line in sink.path.read_text().splitlines()]
    if source == "document":
        egress = [event for event in events if event["event"] == "egress_decision"]
        assert len(egress) == 1
        assert egress[0]["allowed"] is True
        events = [event for event in events if event["event"] == "source_read"]
    assert len(events) == 1
    assert events[0]["event"] == "source_read"
    assert events[0]["status"] == "timeout"
    assert (
        events[0]["stage"]
        == {"workiq": "workiq", "document": "workiq_document", "fabric": "fabric"}[source]
    )
    assert events[0]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_cancelled_turn_clears_cache_even_if_a_child_context_is_retained():
    budget = budget_module()
    context = set_request_context(RequestContext(Principal.anonymous()))
    retained = None
    ready = asyncio.Event()

    async def value():
        return "private document"

    async def child():
        await ready.wait()
        with pytest.raises(TimeoutError):
            await budget.cached_source_read(("document",), value)

    try:
        with pytest.raises(asyncio.CancelledError), budget.turn_budget(30, 1):
            await budget.cached_source_read(("document",), value)
            retained = budget._read_cache.get()
            child_task = asyncio.create_task(child())
            raise asyncio.CancelledError()
        assert retained.values == {}
        assert retained.references == {}
        ready.set()
        await child_task
    finally:
        ready.set()
        reset_request_context(context)
