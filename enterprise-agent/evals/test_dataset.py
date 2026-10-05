import json
from pathlib import Path

import pytest

from enterprise_agent.tools.registry import create_default_registry

DATASET_PATH = Path(__file__).parent / "datasets" / "tool_selection.json"
WORKIQ_DATASET_PATH = Path(__file__).parent / "datasets" / "workiq_tool_selection.json"
FABRIC_DATASET_PATH = Path(__file__).parent / "datasets" / "fabric_tool_selection.json"
MULTI_SOURCE_DATASET_PATH = (
    Path(__file__).parent / "datasets" / "multi_source_tool_selection.json"
)
ACTION_DATASET_PATH = Path(__file__).parent / "datasets" / "controlled_action_selection.json"


def load_cases() -> list[dict[str, object]]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_tool_selection_dataset_has_at_least_twenty_cases() -> None:
    assert len(load_cases()) >= 20


def test_tool_selection_dataset_is_well_formed() -> None:
    cases = load_cases()
    registry_names = create_default_registry().names
    ids = [str(case["id"]) for case in cases]

    assert len(ids) == len(set(ids))
    for case in cases:
        expected = set(case["expected_tools"])  # type: ignore[arg-type]
        forbidden = set(case["forbidden_tools"])  # type: ignore[arg-type]
        assert str(case["prompt"]).strip()
        assert expected <= registry_names
        assert forbidden <= registry_names
        assert expected.isdisjoint(forbidden)


def test_workiq_dataset_is_well_formed() -> None:
    cases = json.loads(WORKIQ_DATASET_PATH.read_text(encoding="utf-8"))
    allowed_tools = {"workiq_fetch", "workiq_get_schema", "workiq_search_paths"}

    assert len(cases) >= 15
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        expected = set(case["expected_tools"])
        forbidden = set(case["forbidden_tools"])
        assert case["prompt"].strip()
        assert expected <= allowed_tools
        assert forbidden <= allowed_tools
        assert expected.isdisjoint(forbidden)


@pytest.mark.parametrize(
    ("path", "minimum", "allowed_tools"),
    [
        (FABRIC_DATASET_PATH, 10, {"fabric_query", "workiq_fetch"}),
        (
            MULTI_SOURCE_DATASET_PATH,
            10,
            {"fabric_query", "workiq_fetch", "create_artifact"},
        ),
        (
            ACTION_DATASET_PATH,
            10,
            {"draft_action", "request_action_approval", "get_action_status"},
        ),
    ],
)
def test_phase_eight_to_ten_datasets_are_well_formed(
    path: Path,
    minimum: int,
    allowed_tools: set[str],
) -> None:
    cases = json.loads(path.read_text(encoding="utf-8"))

    assert len(cases) >= minimum
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        expected = set(case["expected_tools"])
        forbidden = set(case["forbidden_tools"])
        assert case["prompt"].strip()
        assert expected <= allowed_tools
        assert forbidden <= allowed_tools
        assert expected.isdisjoint(forbidden)
