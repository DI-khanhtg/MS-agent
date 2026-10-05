import json
import os
from pathlib import Path

import pytest

from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import RequestContext
from enterprise_agent.security.token_validation import EntraTokenValidator
from evals.evaluators.tool_selection import score_tool_selection

DATASET_PATH = Path(__file__).parent / "datasets" / "multi_source_tool_selection.json"
PASS_RATE_THRESHOLD = 0.80


@pytest.mark.live
@pytest.mark.asyncio
async def test_deepseek_combines_workiq_fabric_and_artifacts() -> None:
    if os.getenv("RUN_MULTI_SOURCE_LIVE_EVALS", "").casefold() != "true":
        pytest.skip("Set RUN_MULTI_SOURCE_LIVE_EVALS=true to run multi-source evaluations")
    incoming_token = os.getenv("FABRIC_USER_ACCESS_TOKEN", "")
    if not incoming_token:
        pytest.skip("Set FABRIC_USER_ACCESS_TOKEN in the process environment")

    settings = Settings()
    required = {"workiq_read", "fabric_read", "artifact_generation"}
    if not required <= settings.tool_groups:
        pytest.skip("Enable Work IQ, Fabric, and artifact tool groups")
    if not (
        settings.deepseek_is_configured
        and settings.workiq_obo_is_configured
        and settings.fabric_obo_is_configured
    ):
        pytest.skip("Configure DeepSeek, Entra, Work IQ, and Fabric in .env")

    principal = await EntraTokenValidator(settings).validate(incoming_token)
    runtime = create_app(settings=settings).state.agent_runtime
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        result = await runtime.chat(
            case["prompt"], f"multi-source-eval-{case['id']}", RequestContext(principal)
        )
        score = score_tool_selection(case, result.tool_calls)
        if not score.passed:
            failures.append(
                {
                    "id": case["id"],
                    "missing": sorted(score.missing),
                    "forbidden_used": sorted(score.forbidden_used),
                    "actual": [call["name"] for call in result.tool_calls],
                }
            )

    pass_rate = (len(cases) - len(failures)) / len(cases)
    assert pass_rate >= PASS_RATE_THRESHOLD, (
        f"Multi-source routing pass rate {pass_rate:.1%} is below "
        f"{PASS_RATE_THRESHOLD:.0%}. Failures: {failures}"
    )
