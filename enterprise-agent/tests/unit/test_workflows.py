from enterprise_agent.agent.instructions import SYSTEM_INSTRUCTIONS


def test_phase_six_read_only_workflows_are_in_agent_policy() -> None:
    for workflow in (
        "PROJECT STATUS",
        "MEETING PREPARATION",
        "RECENT CUSTOMER DISCUSSIONS",
        "REQUIREMENTS REVIEW",
        "OPEN ACTIONS",
    ):
        assert workflow in SYSTEM_INSTRUCTIONS
    assert "must not be sent or published" in SYSTEM_INSTRUCTIONS


def test_prompt_contains_phase_eight_to_ten_routing_and_approval_rules() -> None:
    assert "Do not call Fabric for Microsoft 365-only requests" in SYSTEM_INSTRUCTIONS
    assert "Align project/entity, reporting period, currency" in SYSTEM_INSTRUCTIONS
    assert "authenticated approval API or UI" in SYSTEM_INSTRUCTIONS
    assert "You have no tool that can approve or execute" in SYSTEM_INSTRUCTIONS
    assert "Never delete anything" in SYSTEM_INSTRUCTIONS
