"""Tool-selection scoring independent of any model provider."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSelectionScore:
    passed: bool
    missing: set[str]
    forbidden_used: set[str]


def score_tool_selection(
    case: dict[str, Any], actual_calls: list[dict[str, Any]]
) -> ToolSelectionScore:
    actual = {call["name"] for call in actual_calls}
    expected = set(case["expected_tools"])
    forbidden = set(case["forbidden_tools"])
    missing = expected - actual
    forbidden_used = forbidden & actual
    return ToolSelectionScore(
        passed=not missing and not forbidden_used,
        missing=missing,
        forbidden_used=forbidden_used,
    )
