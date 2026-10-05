import json
import os
from pathlib import Path

import pytest

from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import RequestContext
from enterprise_agent.security.token_validation import EntraTokenValidator
from evals.evaluators.tool_selection import score_tool_selection

DATASET_PATH = Path(__file__).parent / "datasets" / "workiq_tool_selection.json"
PASS_RATE_THRESHOLD = 0.80


@pytest.mark.live
@pytest.mark.asyncio
async def test_deepseek_routes_m365_requests_to_workiq() -> None:
    if os.getenv("RUN_WORKIQ_LIVE_EVALS", "").casefold() != "true":
        pytest.skip("Set RUN_WORKIQ_LIVE_EVALS=true to run Work IQ agent evaluations")
    incoming_token = os.getenv("WORKIQ_USER_ACCESS_TOKEN", "")
    if not incoming_token:
        pytest.skip("Set WORKIQ_USER_ACCESS_TOKEN in the process environment")

    settings = Settings()
    if settings.tool_groups != {"workiq_read"}:
        pytest.skip("Set ENABLED_TOOL_GROUPS=workiq_read for the Work IQ routing evaluation")
    if not settings.deepseek_is_configured or not settings.workiq_obo_is_configured:
        pytest.skip("Configure DeepSeek and Entra credentials in .env")

    principal = await EntraTokenValidator(settings).validate(incoming_token)
    runtime = create_app(settings=settings).state.agent_runtime
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    failures = []

    for case in cases:
        result = await runtime.chat(
            case["prompt"],
            f"workiq-eval-{case['id']}",
            RequestContext(principal),
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
        f"Work IQ routing pass rate {pass_rate:.1%} is below {PASS_RATE_THRESHOLD:.0%}. "
        f"Failures: {failures}"
    )
