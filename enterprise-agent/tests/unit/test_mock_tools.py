import pytest

from enterprise_agent.tools.mock import (
    MockToolError,
    get_current_user,
    get_mock_calendar,
    get_project_status,
    search_mock_documents,
)


def test_get_current_user() -> None:
    result = get_current_user()

    assert result["id"] == "user-001"
    assert result["timezone"] == "Asia/Ho_Chi_Minh"


def test_get_project_status_normalizes_project_prefix() -> None:
    result = get_project_status("Project Alpha")

    assert result["status"] == "at_risk"
    assert result["project_id"] == "alpha"


def test_get_project_status_rejects_unknown_project() -> None:
    with pytest.raises(ValueError, match="Unknown project_id"):
        get_project_status("gamma")


def test_get_project_status_can_simulate_tool_failure() -> None:
    with pytest.raises(MockToolError, match="temporarily unavailable"):
        get_project_status("simulate-failure")


def test_search_documents_returns_ranked_matches() -> None:
    results = search_mock_documents("Alpha requirements")

    assert results
    assert results[0]["project_id"] == "alpha"


@pytest.mark.parametrize("limit", [0, 11])
def test_search_documents_validates_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        search_mock_documents("Alpha", limit=limit)


def test_calendar_filters_iso_date() -> None:
    results = get_mock_calendar("2026-09-13")

    assert [event["id"] for event in results] == ["event-alpha-steering"]


def test_calendar_rejects_invalid_date() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        get_mock_calendar("13/09/2026")

