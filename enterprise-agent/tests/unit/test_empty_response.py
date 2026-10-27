import json
from types import SimpleNamespace

import pytest

from enterprise_agent.agent.runtime import AgentUpstreamError, MicrosoftAgentRuntime
from enterprise_agent.config import Settings
from enterprise_agent.telemetry.operations import AuditLog, audit_sink
from enterprise_agent.tools.recording import get_stream_result, record_tool_call


class AnswerStream:
    def __init__(self, chunks, final_text, finish_reason):
        self.chunks = chunks
        self.final = SimpleNamespace(
            text=final_text,
            finish_reason=finish_reason,
            usage_details={"input_token_count": 12, "output_token_count": 3},
        )

    def __aiter__(self):
        return self.updates()

    async def updates(self):
        for text in self.chunks:
            yield SimpleNamespace(text=text, finish_reason=None)
        yield SimpleNamespace(text="", finish_reason=self.final.finish_reason)

    async def get_final_response(self):
        return self.final


class AnswerAgent:
    def __init__(self, chunks=(), final_text="", finish_reason="stop", tool=False):
        self.chunks, self.final_text, self.finish_reason = chunks, final_text, finish_reason
        self.tool, self.calls = tool, 0

    def create_session(self, **kwargs):
        return {}

    def run(self, message, *, session, stream=False):
        self.calls += 1
        if self.tool:
            record_tool_call("create_artifact", {"private_argument": "must-not-be-logged"})
        output = AnswerStream(self.chunks, self.final_text, self.finish_reason)
        return output if stream else output.get_final_response()


def runtime(agent):
    return MicrosoftAgentRuntime(
        Settings(_env_file=None, gemini_max_retries=2, enabled_tool_groups="artifact_generation"),
        agent=agent,
    )


async def answer(instance, stream):
    if stream:
        return "".join([text async for text in instance.stream_chat("hello", "session")])
    return (await instance.chat("hello", "session")).text


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", ["", " \n\t"])
@pytest.mark.parametrize("tool", [False, True])
async def test_empty_answer_is_an_error_without_replaying_tools(stream, text, tool):
    agent = AnswerAgent(chunks=[text], final_text=text, tool=tool)
    instance = runtime(agent)
    with pytest.raises(AgentUpstreamError) as error:
        await answer(instance, stream)
    assert type(error.value).__name__ == "AgentEmptyResponseError"
    assert agent.calls == 1
    assert instance._circuit.failures == 1
    if stream:
        assert get_stream_result() == {"tool_calls": [], "usage": {}}


@pytest.mark.asyncio
async def test_final_text_recovers_an_empty_stream_without_a_second_model_call():
    agent = AnswerAgent(chunks=[""], final_text="The report is ready.", tool=True)
    assert await answer(runtime(agent), True) == "The report is ready."
    assert agent.calls == 1
    assert get_stream_result()["usage"] == {"input_token_count": 12, "output_token_count": 3}
    assert get_stream_result()["tool_calls"][0]["name"] == "create_artifact"


@pytest.mark.asyncio
async def test_final_text_does_not_duplicate_streamed_answer():
    agent = AnswerAgent(chunks=["The report ", "is ready."], final_text="The report is ready.")
    assert await answer(runtime(agent), True) == "The report is ready."


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "reason", ["stop", "length", "MALFORMED_FUNCTION_CALL", "private-diagnostic"]
)
async def test_empty_answer_audits_finish_reason_without_private_data(tmp_path, stream, reason):
    sink = AuditLog(tmp_path / "audit.jsonl")
    token = audit_sink.set(sink)
    try:
        with pytest.raises(AgentUpstreamError):
            await answer(runtime(AnswerAgent(finish_reason=reason, tool=True)), stream)
    finally:
        audit_sink.reset(token)
    events = [json.loads(line) for line in sink.path.read_text().splitlines()]
    turn = next(event for event in events if event["event"] == "agent_turn")
    assert turn["status"] == "error"
    assert turn["finish_reason"] == ("other" if reason == "private-diagnostic" else reason.lower())
    assert "private-diagnostic" not in sink.path.read_text()
    assert "must-not-be-logged" not in sink.path.read_text()
