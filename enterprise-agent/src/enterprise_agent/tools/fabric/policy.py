"""Read-only policy for natural-language Fabric Data Agent queries."""

MAX_QUESTION_CHARS = 4_000


class FabricPolicyError(ValueError):
    """A Fabric query violates the application policy."""


def validate_fabric_question(question: str) -> str:
    normalized = question.strip()
    if not normalized or len(normalized) > MAX_QUESTION_CHARS:
        raise FabricPolicyError(
            f"Fabric question must contain between 1 and {MAX_QUESTION_CHARS} characters"
        )
    return normalized

