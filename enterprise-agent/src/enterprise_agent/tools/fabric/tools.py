"""Agent-facing read-only Fabric Data Agent tool."""

from typing import Any

from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.fabric.auth import FabricTokenProvider
from enterprise_agent.tools.fabric.client import FabricDataAgentClient
from enterprise_agent.tools.fabric.policy import validate_fabric_question
from enterprise_agent.tools.recording import record_tool_call


class FabricReadTools:
    def __init__(
        self,
        client: FabricDataAgentClient,
        token_provider: FabricTokenProvider,
        egress_gate: EgressGate,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.egress_gate = egress_gate

    async def fabric_query(self, question: str) -> dict[str, Any]:
        """Query the published Fabric Data Agent for structured business metrics.

        Use for revenue, cost, budget versus actual, utilization, KPIs, historical project
        data, and operational metrics. Do not use for email, calendar, Teams, files, people,
        or other Microsoft 365 communication data.
        """
        validated_question = validate_fabric_question(question)
        record_tool_call("fabric_query", {"question": validated_question})
        context = get_request_context()
        token = await self.token_provider.get_token(context.principal)
        result = await self.client.query(validated_question, access_token=token)
        return self.egress_gate.enforce(
            result,
            source="fabric:data_agent",
            principal=context.principal,
        )

