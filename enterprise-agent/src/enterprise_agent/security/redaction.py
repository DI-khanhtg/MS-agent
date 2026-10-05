"""Recursive classification, redaction, and bounded-copy helpers."""

import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SanitizationStats:
    redaction_count: int = 0
    truncated: bool = False


def find_prohibited_classifications(
    value: Any,
    *,
    sensitive_key_patterns: tuple[str, ...],
    secret_patterns: tuple[re.Pattern[str], ...],
) -> set[str]:
    classifications: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                separated_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
                normalized_key = separated_key.casefold().replace("-", "_")
                if child not in (None, "") and any(
                    _key_matches(normalized_key, pattern)
                    for pattern in sensitive_key_patterns
                ):
                    classifications.add("credential")
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str) and any(pattern.search(item) for pattern in secret_patterns):
            classifications.add("secret")

    visit(value)
    return classifications


def _key_matches(normalized_key: str, pattern: str) -> bool:
    return (
        normalized_key == pattern
        or normalized_key.startswith(f"{pattern}_")
        or normalized_key.endswith(f"_{pattern}")
        or f"_{pattern}_" in normalized_key
    )


def sanitize_copy(
    value: Any,
    *,
    pii_patterns: tuple[re.Pattern[str], ...],
    replacement: str,
    max_list_items: int,
    max_string_chars: int,
    max_depth: int,
    stats: SanitizationStats,
    depth: int = 0,
) -> Any:
    if depth >= max_depth:
        stats.truncated = True
        return "[TRUNCATED:MAX_DEPTH]"
    if isinstance(value, dict):
        return {
            str(key): sanitize_copy(
                child,
                pii_patterns=pii_patterns,
                replacement=replacement,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
                max_depth=max_depth,
                stats=stats,
                depth=depth + 1,
            )
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > max_list_items:
            stats.truncated = True
        return [
            sanitize_copy(
                child,
                pii_patterns=pii_patterns,
                replacement=replacement,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
                max_depth=max_depth,
                stats=stats,
                depth=depth + 1,
            )
            for child in value[:max_list_items]
        ]
    if isinstance(value, str):
        sanitized = value
        for pattern in pii_patterns:
            sanitized, replacements = pattern.subn(replacement, sanitized)
            stats.redaction_count += replacements
        if len(sanitized) > max_string_chars:
            sanitized = f"{sanitized[:max_string_chars]}…[TRUNCATED]"
            stats.truncated = True
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "model_dump"):
        return sanitize_copy(
            value.model_dump(mode="json"),
            pii_patterns=pii_patterns,
            replacement=replacement,
            max_list_items=max_list_items,
            max_string_chars=max_string_chars,
            max_depth=max_depth,
            stats=stats,
            depth=depth,
        )
    return sanitize_copy(
        str(value),
        pii_patterns=pii_patterns,
        replacement=replacement,
        max_list_items=max_list_items,
        max_string_chars=max_string_chars,
        max_depth=max_depth,
        stats=stats,
        depth=depth,
    )
