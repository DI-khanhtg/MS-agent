import json
import os
from pathlib import Path

import pytest

from enterprise_agent.agent.runtime import MicrosoftAgentRuntime
from enterprise_agent.config import Settings
from evals.evaluators.tool_selection import score_tool_selection

DATASET_PATH = Path(__file__).parent / "datasets" / "tool_selection.json"
PASS_RATE_THRESHOLD = 0.80


@pytest.mark.live
@pytest.mark.asyncio
async def test_deepseek_tool_selection_pass_rate() -> None:
    if os.getenv("RUN_LIVE_EVALS", "").lower() != "true":
        pytest.skip("Set RUN_LIVE_EVALS=true to allow paid external API calls")

    settings = Settings()
    if not settings.deepseek_is_configured:
        pytest.skip("Configure DEEPSEEK_API_KEY in .env before running live evaluations")

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    runtime = MicrosoftAgentRuntime(settings)
    failures = []

    for case in cases:
        result = await runtime.chat(case["prompt"], f"eval-{case['id']}")
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
        f"Tool-selection pass rate {pass_rate:.1%} is below {PASS_RATE_THRESHOLD:.0%}. "
        f"Failures: {failures}"
    )

