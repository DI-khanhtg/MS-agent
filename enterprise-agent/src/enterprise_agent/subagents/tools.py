"""Agent Framework tools that delegate bounded work to specialist sub-agents."""

import re
from typing import Any

from enterprise_agent.agent.budget import read_window, reserve_source_call
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.subagents.client import SubAgentClient, SubAgentError
from enterprise_agent.subagents.roles import REVIEWER, WRITER, SubAgentRole
from enterprise_agent.tools.recording import record_tool_call

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_DIGITS = re.compile(r"\d+")


def _unavailable(code: str, message: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "error": {"code": code, "message": message},
        "next_step": "Continue with your own draft and do not claim it was reviewed or edited.",
    }


class SubAgentTools:
    def __init__(
        self, settings: Settings, egress_gate: EgressGate, client: SubAgentClient | None = None
    ) -> None:
        self.settings = settings
        self.egress_gate = egress_gate
        self.client = client or SubAgentClient(settings)

    async def review_draft(self, task: str, draft: str, evidence: str = "") -> dict[str, Any]:
        """Have an independent reviewer fact-check a draft before you finalize it.

        Use before delivering a source-based report, analysis, figures-heavy answer, or before
        create_artifact for such content. Do not use for short conversational replies.
        task: what the user asked for. draft: the complete answer or document text you plan to
        deliver. evidence: the retrieved facts and excerpts the draft relies on (exact numbers,
        names, quotes); without evidence only consistency and task coverage can be checked.
        The reviewer cannot read any source and only sees what you pass. Its findings are
        advice: verify each against your evidence, fix real problems, and then deliver.
        """
        reply = await self._delegate(
            REVIEWER,
            "review_draft",
            {"task": task, "draft": draft, "evidence": evidence},
        )
        if reply["status"] == "ok":
            reply["review"] = reply.pop("text")
        return reply

    async def polish_writing(
        self, draft: str, audience: str = "", language: str = "", style: str = ""
    ) -> dict[str, Any]:
        """Have a writing specialist improve the clarity, structure and tone of a draft.

        Use for long-form deliverables: reports, proposals, summaries, emails, slide text.
        Skip it for short answers. draft: the complete text, content already verified.
        audience: who will read it. language: for example "Vietnamese" or "English".
        style: tone or format hints such as "formal executive summary". The result keeps all
        facts, numbers and links; if missing_from_polished is non-empty, restore those items.
        Use the returned polished_text as the final text or as the artifact section content.
        """
        reply = await self._delegate(
            WRITER,
            "polish_writing",
            {"audience": audience, "language": language, "style": style, "draft": draft},
        )
        if reply["status"] != "ok":
            return reply
        polished = reply.pop("text")
        reply["polished_text"] = polished
        reply["missing_from_polished"] = _missing_tokens(draft, polished)
        if reply["missing_from_polished"]:
            reply["warning"] = "Restore the missing items or keep your original wording for them."
        return reply

    async def _delegate(
        self, role: SubAgentRole, tool_name: str, fields: dict[str, str]
    ) -> dict[str, Any]:
        record_tool_call(tool_name, {"role": role.name})
        if not fields.get("draft", "").strip():
            return _unavailable("empty_draft", "Provide the draft text.")
        if sum(len(value) for value in fields.values()) > self.settings.subagent_max_input_chars:
            return _unavailable(
                "input_too_large",
                f"Send at most {self.settings.subagent_max_input_chars} characters in total; "
                "pass a shorter draft or only the key evidence.",
            )
        if not reserve_source_call(("subagent",), self.settings.subagent_max_calls_per_turn):
            return _unavailable("turn_limit", "The sub-agent call limit for this turn is used up.")
        try:
            # The model may have copied sensitive text from user input into these arguments,
            # so the payload passes the same gate as any other outbound enterprise text.
            gated = self.egress_gate.enforce(
                fields, source="subagent:input", principal=get_request_context().principal
            )
        except EgressBlockedError:
            return _unavailable("egress_blocked", "The content cannot be sent to the sub-agent.")
        if gated["_egress"]["truncated"]:
            return _unavailable(
                "input_truncated",
                "The egress policy would truncate this input. Keep the complete original draft; "
                "send shorter sections separately if specialist help is needed.",
            )
        safe = gated["data"]
        try:
            async with read_window(f"subagent_{role.name}"):
                reply = await self.client.run(role, _render(safe))
        except (SubAgentError, TimeoutError):
            return _unavailable(
                "subagent_failed", "The specialist did not respond in time or is unavailable."
            )
        return {"status": "ok", "role": role.name, "text": reply.text}


def _render(fields: dict[str, str]) -> str:
    return "\n".join(
        f"<{key}>\n{value}\n</{key}>" for key, value in fields.items() if str(value).strip()
    )


def _missing_tokens(original: str, rewritten: str) -> list[str]:
    """List URLs and digit runs from the draft that the rewrite dropped."""
    missing = [url for url in dict.fromkeys(_URL.findall(original)) if url not in rewritten]
    kept = set(_DIGITS.findall(rewritten))
    missing += [n for n in dict.fromkeys(_DIGITS.findall(original)) if n not in kept]
    return missing[:20]
