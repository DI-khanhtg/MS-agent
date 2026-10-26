"""Preserve Gemini metadata through Agent Framework's Chat Completions hooks."""

from typing import Any

from agent_framework import Content, Message


def parse_gemini_contents(message: Any, contents: list[Content]) -> list[Content]:
    """Keep tool-call thought signatures, including those received in stream deltas."""
    calls = [content for content in contents if content.type == "function_call"]
    tools = [
        tool
        for tool in (getattr(message, "tool_calls", None) or [])
        if getattr(tool, "function", None) is not None
    ]
    for content, tool in zip(calls, tools, strict=True):
        extra = getattr(tool, "extra_content", None)
        if isinstance(extra, dict):
            content.additional_properties["gemini_extra_content"] = extra
    return contents


def prepare_gemini_message(
    message: Message, prepared: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Echo opaque metadata on the original call, also after session restoration."""
    metadata = {
        content.call_id: content.additional_properties["gemini_extra_content"]
        for content in message.contents
        if content.type == "function_call"
        and "gemini_extra_content" in content.additional_properties
    }
    for item in prepared:
        for call in item.get("tool_calls", []):
            if call["id"] in metadata:
                call["extra_content"] = metadata[call["id"]]
    return prepared
