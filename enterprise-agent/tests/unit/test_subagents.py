from types import SimpleNamespace
from typing import Any

import pytest

from enterprise_agent.agent.budget import turn_budget
from enterprise_agent.agent.factory import create_enterprise_agent
from enterprise_agent.agent.instructions import build_system_instructions
from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.subagents.client import SubAgentClient, SubAgentError, SubAgentReply
from enterprise_agent.subagents.tools import SubAgentTools
from enterprise_agent.tools.recording import begin_tool_recording, end_tool_recording
from enterprise_agent.tools.registry import create_default_registry


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, gemini_api_key="main-key", **overrides)


class FakeClient:
    def __init__(self, text: str = "ok text", error: Exception | None = None) -> None:
        self.text, self.error, self.calls = text, error, []

    async def run(self, role: Any, content: str) -> SubAgentReply:
        self.calls.append((role.name, content))
        if self.error:
            raise self.error
        return SubAgentReply(self.text, 1, 1)


@pytest.fixture
def context():
    token = set_request_context(RequestContext(Principal.anonymous()))
    recording = begin_tool_recording(24)
    try:
        with turn_budget(60, 5):
            yield
    finally:
        end_tool_recording(recording)
        reset_request_context(token)


def _tools(client: FakeClient, **overrides: Any) -> SubAgentTools:
    gate = EgressGate.from_yaml(PROJECT_ROOT / "config" / "egress.yaml")
    return SubAgentTools(_settings(subagent_api_key="sub-key", **overrides), gate, client)  # type: ignore[arg-type]


def test_subagent_groups_follow_the_key_and_ignore_manual_entries() -> None:
    assert "subagents" not in _settings().tool_groups
    assert "subagents" not in _settings(enabled_tool_groups="subagents,mock_read").tool_groups
    assert "subagents" in _settings(subagent_api_key="sub-key").tool_groups
    assert (
        "subagents" not in _settings(subagent_api_key="sub-key", subagent_enabled=False).tool_groups
    )
    assert "subagents" not in _settings(subagent_api_key="sub-key", runtime_mode="demo").tool_groups


def test_subagent_endpoint_and_model_default_to_the_main_agent() -> None:
    settings = _settings(subagent_api_key="sub-key")
    assert settings.subagent_endpoint == settings.gemini_base_url
    assert settings.subagent_model_name == settings.gemini_model
    custom = _settings(subagent_model="m2", subagent_base_url="https://example.test/v1/")
    assert (custom.subagent_model_name, custom.subagent_endpoint) == (
        "m2",
        "https://example.test/v1",
    )


def test_factory_exposes_subagent_tools_only_when_configured() -> None:
    class Tools:
        async def review_draft(self, task: str, draft: str, evidence: str = "") -> dict:
            return {}

        async def polish_writing(self, draft: str) -> dict:
            return {}

    configured = _settings(subagent_api_key="sub-key", enabled_tool_groups="mock_read")
    agent = create_enterprise_agent(configured, create_default_registry(subagent_tools=Tools()))  # type: ignore[arg-type]
    names = {tool.name for tool in agent.default_options["tools"]}
    assert {"review_draft", "polish_writing"} <= names
    assert "review_draft" in agent.default_options["instructions"]

    plain = create_enterprise_agent(
        _settings(enabled_tool_groups="mock_read"), create_default_registry()
    )
    assert not {"review_draft", "polish_writing"} & {t.name for t in plain.default_options["tools"]}
    assert "review_draft" not in build_system_instructions(_settings().tool_groups)


@pytest.mark.asyncio
async def test_review_draft_returns_review_and_sends_tagged_content(context) -> None:
    client = FakeClient("VERDICT: PASS")
    result = await _tools(client).review_draft("Summarize risks", "Risk A is high.", "Risk A: high")

    assert result == {"status": "ok", "role": "reviewer", "review": "VERDICT: PASS"}
    role, content = client.calls[0]
    assert role == "reviewer"
    assert "<draft>\nRisk A is high.\n</draft>" in content
    assert "<evidence>\nRisk A: high\n</evidence>" in content


@pytest.mark.asyncio
async def test_polish_writing_flags_dropped_links_and_numbers(context) -> None:
    draft = "Revenue grew 12% in 2026. See https://example.com/report for details."
    client = FakeClient("Revenue rose in 2026.")
    result = await _tools(client).polish_writing(draft, language="English")

    assert result["status"] == "ok"
    assert result["polished_text"] == "Revenue rose in 2026."
    assert result["missing_from_polished"] == ["https://example.com/report", "12"]
    assert "warning" in result

    clean = await _tools(FakeClient(draft)).polish_writing(draft)
    assert clean["missing_from_polished"] == []
    assert "warning" not in clean


@pytest.mark.asyncio
async def test_subagent_failure_degrades_without_raising(context) -> None:
    result = await _tools(FakeClient(error=SubAgentError("boom"))).review_draft("t", "draft")

    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "subagent_failed"
    assert "boom" not in str(result)


@pytest.mark.asyncio
async def test_limits_and_empty_input_never_reach_the_provider(context) -> None:
    client = FakeClient()
    tools = _tools(client, subagent_max_calls_per_turn=1, subagent_max_input_chars=1000)

    assert (await tools.review_draft("t", "  "))["error"]["code"] == "empty_draft"
    assert (await tools.review_draft("t", "x" * 1001))["error"]["code"] == "input_too_large"
    assert (await tools.review_draft("t", "first"))["status"] == "ok"
    assert (await tools.review_draft("t", "second"))["error"]["code"] == "turn_limit"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_prohibited_content_is_blocked_before_leaving_the_service(context) -> None:
    client = FakeClient()
    secret = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    result = await _tools(client).polish_writing(f"Key follows {secret}")

    assert result["error"]["code"] == "egress_blocked"
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["draft", "evidence", "task"])
async def test_truncated_input_never_produces_a_partial_review(context, field) -> None:
    client = FakeClient("VERDICT: PASS")
    fields = {"task": "Summarize", "draft": "Full draft.", "evidence": "Source facts."}
    fields[field] = "Plain text. " * 500

    result = await _tools(client).review_draft(**fields)

    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "input_truncated"
    assert client.calls == []


@pytest.mark.asyncio
async def test_truncated_draft_never_replaces_complete_writing(context) -> None:
    client = FakeClient("Only the beginning.")
    result = await _tools(client).polish_writing("Plain text. " * 500)

    assert result["status"] == "unavailable"
    assert result["error"]["code"] == "input_truncated"
    assert "polished_text" not in result
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
async def test_client_rejects_incomplete_provider_answers(finish_reason) -> None:
    class Completions:
        async def create(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Partial prose."),
                        finish_reason=finish_reason,
                    )
                ],
                usage=None,
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    from enterprise_agent.subagents.roles import WRITER

    with pytest.raises(SubAgentError):
        await SubAgentClient(_settings(), fake).run(WRITER, "Complete draft.")


@pytest.mark.asyncio
async def test_client_uses_the_second_key_and_wraps_provider_errors() -> None:
    created: dict[str, Any] = {}

    class Completions:
        async def create(self, **kwargs: Any) -> Any:
            created.update(kwargs)
            message = SimpleNamespace(content="  edited  ")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    from enterprise_agent.subagents.roles import WRITER

    reply = await SubAgentClient(_settings(subagent_model="writer-model"), fake).run(WRITER, "hi")
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("edited", 3, 2)
    assert created["model"] == "writer-model"
    assert created["messages"][0]["content"] == WRITER.instructions

    real = SubAgentClient(_settings(subagent_api_key="sub-key"))
    try:
        assert real._get_client().api_key == "sub-key"
    finally:
        await real._get_client().close()
    with pytest.raises(SubAgentError):
        await SubAgentClient(_settings()).run(WRITER, "hi")
