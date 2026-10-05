"""Microsoft Agent Framework construction isolated from application code."""

from typing import Any

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient

from enterprise_agent.agent.instructions import SYSTEM_INSTRUCTIONS
from enterprise_agent.config import Settings
from enterprise_agent.tools.registry import ToolRegistry


def create_deepseek_client(settings: Settings) -> OpenAIChatClient:
    """Build the reusable OpenAI-compatible Responses API client."""
    if not settings.deepseek_is_configured:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    return OpenAIChatClient(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )


def create_enterprise_agent(
    settings: Settings,
    registry: ToolRegistry,
    *,
    client: Any | None = None,
) -> Agent:
    """Build an isolated agent using a reusable Responses API client."""
    agent_client = client or create_deepseek_client(settings)
    return Agent(
        name="enterprise-agent",
        description="General-purpose enterprise work agent",
        client=agent_client,
        instructions=SYSTEM_INSTRUCTIONS,
        tools=registry.tools_for_groups(settings.tool_groups),
    )
