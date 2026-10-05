import pytest

from enterprise_agent.tools.workiq.policy import (
    WorkIQPolicyError,
    validate_entity_urls,
    validate_path_filter,
    validate_read_path,
    validate_schema_request,
    validate_tool_name,
)


@pytest.mark.parametrize(
    "path",
    [
        "/me/messages?$top=10",
        "/me/events/event-id",
        "/users/user-id?$select=id,displayName",
        "/sites/site-id/drive/root/children?$top=25",
    ],
)
def test_read_paths_are_allowlisted(path: str) -> None:
    assert validate_read_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "https://graph.microsoft.com/v1.0/me/messages",
        "/groups/group-id/members",
        "/me/../servicePrincipals",
        "/users/user-id/authentication/methods",
        "/me/messages?$skip=10",
        "/me/messages?$skiptoken=opaque",
        "/me/messages?$top=100",
    ],
)
def test_unsafe_paths_are_blocked(path: str) -> None:
    with pytest.raises(WorkIQPolicyError):
        validate_read_path(path)


def test_entity_url_batch_is_bounded() -> None:
    with pytest.raises(WorkIQPolicyError, match="between 1 and 10"):
        validate_entity_urls(["/me/messages"] * 11)


def test_write_tools_are_not_enabled() -> None:
    for name in ("create_entity", "update_entity", "delete_entity", "do_action"):
        with pytest.raises(WorkIQPolicyError, match="not enabled"):
            validate_tool_name(name)


def test_schema_discovery_is_fetch_only() -> None:
    with pytest.raises(WorkIQPolicyError, match="Only fetch"):
        validate_schema_request("/me/messages", "update")


def test_path_filter_is_bounded() -> None:
    assert validate_path_filter(" messages ") == "messages"
    with pytest.raises(WorkIQPolicyError):
        validate_path_filter("")

