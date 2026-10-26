"""Microsoft Agent Framework construction isolated from application code."""

from typing import Any

from agent_framework import Agent, SlidingWindowStrategy
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from enterprise_agent.agent.gemini import parse_gemini_contents, prepare_gemini_message
from enterprise_agent.agent.instructions import build_system_instructions
from enterprise_agent.config import Settings
from enterprise_agent.tools.registry import ToolRegistry


def create_gemini_client(settings: Settings) -> OpenAIChatCompletionClient:
    """Build Google's OpenAI-compatible Chat Completions client."""
    if not settings.gemini_is_configured:
        raise ValueError("GEMINI_API_KEY is not configured")
    return OpenAIChatCompletionClient(
        async_client=AsyncOpenAI(
            api_key=settings.gemini_api_key.get_secret_value(),
            base_url=settings.gemini_base_url,
            timeout=settings.gemini_timeout_seconds,
            max_retries=0,
        ),
        model=settings.gemini_model,
        instruction_role="system",
        response_parser=parse_gemini_contents,
        message_preparer=prepare_gemini_message,
        function_invocation_configuration={
            "max_iterations": settings.max_tool_calls,
            "max_function_calls": settings.max_tool_calls,
            "max_duration_seconds": settings.agent_turn_timeout_seconds,
        },
    )


def create_enterprise_agent(
    settings: Settings,
    registry: ToolRegistry,
    *,
    client: Any | None = None,
) -> Agent:
    """Build an isolated agent using a reusable Gemini client."""
    agent_client = client or create_gemini_client(settings)
    return Agent(
        name="enterprise-agent",
        description="General-purpose enterprise work agent",
        client=agent_client,
        instructions=build_system_instructions(settings.tool_groups),
        tools=registry.tools_for_groups(settings.tool_groups),
        compaction_strategy=SlidingWindowStrategy(keep_last_groups=settings.max_history_messages),
    )
