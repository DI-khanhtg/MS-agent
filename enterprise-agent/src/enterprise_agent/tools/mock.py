"""Deterministic, read-only tools used to validate agent coordination."""

from datetime import date
from typing import Any

from enterprise_agent.tools.recording import record_tool_call


class MockToolError(RuntimeError):
    """An intentional mock backend failure."""


_PROJECTS: dict[str, dict[str, Any]] = {
    "alpha": {
        "project_id": "alpha",
        "name": "Project Alpha",
        "status": "at_risk",
        "progress_percent": 68,
        "open_actions": [
            "Confirm security review owner",
            "Recover the delayed data migration milestone",
        ],
        "last_updated": "2026-09-11",
    },
    "beta": {
        "project_id": "beta",
        "name": "Project Beta",
        "status": "on_track",
        "progress_percent": 42,
        "open_actions": ["Approve the user-research plan"],
        "last_updated": "2026-09-10",
    },
}

_DOCUMENTS = [
    {
        "id": "doc-alpha-requirements",
        "title": "Project Alpha requirements",
        "project_id": "alpha",
        "snippet": "SSO, audit logging, and a November pilot are required.",
        "updated_at": "2026-09-09",
    },
    {
        "id": "doc-alpha-risk-register",
        "title": "Project Alpha risk register",
        "project_id": "alpha",
        "snippet": "Data migration and security review are the current high risks.",
        "updated_at": "2026-09-11",
    },
    {
        "id": "doc-beta-brief",
        "title": "Project Beta discovery brief",
        "project_id": "beta",
        "snippet": "Discovery focuses on finance operations and reporting.",
        "updated_at": "2026-09-08",
    },
]

_CALENDAR = [
    {
        "id": "event-alpha-steering",
        "title": "Project Alpha steering committee",
        "start": "2026-09-13T09:00:00+07:00",
        "attendees": ["An Nguyen", "Linh Tran"],
    },
    {
        "id": "event-beta-review",
        "title": "Project Beta discovery review",
        "start": "2026-09-14T14:00:00+07:00",
        "attendees": ["An Nguyen", "Minh Le"],
    },
]


def get_current_user() -> dict[str, Any]:
    """Get the signed-in mock user's profile, locale, role, and timezone."""
    record_tool_call("get_current_user", {})
    return {
        "id": "user-001",
        "display_name": "An Nguyen",
        "email": "an.nguyen@example.com",
        "role": "Program Manager",
        "locale": "vi-VN",
        "timezone": "Asia/Ho_Chi_Minh",
    }


def get_project_status(project_id: str) -> dict[str, Any]:
    """Get authoritative mock status and open actions for a project ID such as alpha or beta."""
    normalized_id = project_id.strip().lower().removeprefix("project ")
    record_tool_call("get_project_status", {"project_id": project_id})
    if normalized_id == "simulate-failure":
        raise MockToolError("The mock project service is temporarily unavailable.")
    if normalized_id not in _PROJECTS:
        raise ValueError(f"Unknown project_id: {project_id}")
    return _PROJECTS[normalized_id]


def search_mock_documents(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search mock enterprise documents by keywords; limit must be between 1 and 10."""
    record_tool_call("search_mock_documents", {"query": query, "limit": limit})
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    terms = {term.casefold() for term in query.split() if len(term) > 2}
    matches = []
    for document in _DOCUMENTS:
        haystack = " ".join(str(value) for value in document.values()).casefold()
        score = sum(term in haystack for term in terms)
        if score:
            matches.append((score, document))
    matches.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
    return [document for _, document in matches[:limit]]


def get_mock_calendar(calendar_date: str = "") -> list[dict[str, Any]]:
    """Get mock calendar events, optionally filtered by an ISO date in YYYY-MM-DD format."""
    record_tool_call("get_mock_calendar", {"calendar_date": calendar_date})
    if not calendar_date:
        return _CALENDAR
    try:
        parsed_date = date.fromisoformat(calendar_date)
    except ValueError as exc:
        raise ValueError("calendar_date must use YYYY-MM-DD") from exc
    return [event for event in _CALENDAR if event["start"].startswith(parsed_date.isoformat())]

