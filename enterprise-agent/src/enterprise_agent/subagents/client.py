"""Single-call chat client for specialist sub-agents using their own API key."""

import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from enterprise_agent.config import Settings
from enterprise_agent.subagents.roles import SubAgentRole

logger = logging.getLogger(__name__)


class SubAgentError(RuntimeError):
    """The sub-agent could not produce an answer; the primary agent should continue alone."""


@dataclass(frozen=True, slots=True)
class SubAgentReply:
    text: str
    input_tokens: int
    output_tokens: int


class SubAgentClient:
    """Lazily built so an unconfigured key never affects the primary agent."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.settings.subagent_is_configured:
                raise SubAgentError("SUBAGENT_API_KEY is not configured")
            self._client = AsyncOpenAI(
                api_key=self.settings.subagent_api_key.get_secret_value(),
                base_url=self.settings.subagent_endpoint,
                timeout=self.settings.subagent_timeout_seconds,
                max_retries=self.settings.subagent_max_retries,
            )
        return self._client

    async def run(self, role: SubAgentRole, content: str) -> SubAgentReply:
        try:
            response = await self._get_client().chat.completions.create(
                model=self.settings.subagent_model_name,
                messages=[
                    {"role": "system", "content": role.instructions},
                    {"role": "user", "content": content},
                ],
                temperature=role.temperature,
                max_tokens=role.max_output_tokens,
            )
        except SubAgentError:
            raise
        except Exception as exc:  # Provider errors must degrade, not fail the user's turn.
            logger.warning("Sub-agent request failed", extra={"role": role.name})
            raise SubAgentError("The sub-agent request failed") from exc
        choice = response.choices[0] if response.choices else None
        if choice and getattr(choice, "finish_reason", "stop") != "stop":
            raise SubAgentError("The sub-agent returned an incomplete answer")
        text = ((choice.message.content if choice else None) or "").strip()
        if not text:
            raise SubAgentError("The sub-agent returned no answer")
        usage = getattr(response, "usage", None)
        reply = SubAgentReply(
            text,
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )
        logger.info(
            "subagent_usage",
            extra={
                "role": role.name,
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
            },
        )
        return reply
