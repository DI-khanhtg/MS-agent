from dataclasses import replace

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import Principal


@pytest.fixture
def gate() -> EgressGate:
    settings = Settings(_env_file=None)
    return EgressGate.from_yaml(settings.egress_policy_path)


@pytest.fixture
def principal() -> Principal:
    return Principal(
        tenant_id="tenant-a",
        object_id="user-a",
        subject="subject-a",
        username="user@example.com",
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-token",
    )


def test_normal_payload_is_allowed(gate: EgressGate) -> None:
    decision = gate.evaluate(
        {"subject": "Project Alpha update", "status": "on track", "secretary": "Alex"}
    )

    assert decision.allowed is True
    assert decision.payload["status"] == "on track"
    assert decision.payload["secretary"] == "Alex"


def test_configured_pii_is_redacted(gate: EgressGate) -> None:
    decision = gate.evaluate(
        {"body": "Email jane.doe@example.com or call +84 912 345 678 for details."}
    )

    assert decision.allowed is True
    assert "jane.doe@example.com" not in decision.payload["body"]
    assert "+84 912 345 678" not in decision.payload["body"]
    assert decision.redaction_count == 2
    assert "pii" in decision.classifications


@pytest.mark.parametrize(
    "payload",
    [
        {"client_secret": "super-secret-value"},
        {"body": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"},
        {"body": "eyJabcdefghijk.abcdefghijklmnopqrstuvwxyz.0123456789abcdef"},
    ],
)
def test_credentials_and_secrets_are_blocked(gate: EgressGate, payload: object) -> None:
    decision = gate.evaluate(payload)

    assert decision.allowed is False
    assert decision.reason == "prohibited_classification"
    assert decision.payload is None


def test_collection_and_string_limits_truncate(gate: EgressGate) -> None:
    decision = gate.evaluate({"rows": list(range(100)), "body": "x" * 5000})

    assert decision.allowed is True
    assert len(decision.payload["rows"]) == 25
    assert len(decision.payload["body"]) < 4100
    assert decision.truncated is True


def test_payload_over_hard_byte_limit_is_blocked(gate: EgressGate) -> None:
    restrictive_gate = EgressGate(replace(gate.policy, max_payload_bytes=20))

    decision = restrictive_gate.evaluate({"message": "This is longer than twenty bytes"})

    assert decision.allowed is False
    assert decision.reason == "payload_too_large"


def test_enforce_returns_only_sanitized_data(
    gate: EgressGate,
    principal: Principal,
) -> None:
    output = gate.enforce(
        {"from": "private@example.com", "subject": "Status"},
        source="workiq:fetch",
        principal=principal,
    )

    assert output["data"]["from"] == "[REDACTED]"
    assert output["_egress"]["redactions"] == 1
    assert output["_egress"]["source_classification"] == "enterprise_workiq"


def test_enforce_raises_without_returning_secret(
    gate: EgressGate,
    principal: Principal,
) -> None:
    with pytest.raises(EgressBlockedError, match="prohibited_classification") as error:
        gate.enforce(
            {"access_token": "must-never-leave"},
            source="workiq:fetch",
            principal=principal,
        )

    assert "must-never-leave" not in str(error.value)
