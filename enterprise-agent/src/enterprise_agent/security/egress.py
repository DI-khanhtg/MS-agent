"""Enterprise-data egress gate applied before tool results reach DeepSeek."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enterprise_agent.security.identity import Principal
from enterprise_agent.security.redaction import (
    SanitizationStats,
    find_prohibited_classifications,
    sanitize_copy,
)

logger = logging.getLogger(__name__)


class EgressBlockedError(RuntimeError):
    """A payload cannot be sent to the external model under the active policy."""


@dataclass(frozen=True, slots=True)
class EgressDecision:
    allowed: bool
    payload: Any | None
    classifications: frozenset[str]
    redaction_count: int
    truncated: bool
    original_bytes: int
    output_bytes: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    max_payload_bytes: int
    max_list_items: int
    max_string_chars: int
    max_depth: int
    replacement: str
    sensitive_key_patterns: tuple[str, ...]
    secret_patterns: tuple[re.Pattern[str], ...]
    pii_patterns: tuple[re.Pattern[str], ...]
    prohibited_classifications: frozenset[str]

    @classmethod
    def from_yaml(cls, path: Path) -> "EgressPolicy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        limits = raw["limits"]
        redaction = raw["redaction"]
        blocking = raw["blocking"]
        return cls(
            max_payload_bytes=int(limits["max_payload_bytes"]),
            max_list_items=int(limits["max_list_items"]),
            max_string_chars=int(limits["max_string_chars"]),
            max_depth=int(limits["max_depth"]),
            replacement=str(redaction["replacement"]),
            sensitive_key_patterns=tuple(
                str(item).casefold().replace("-", "_")
                for item in redaction["sensitive_key_patterns"]
            ),
            secret_patterns=tuple(
                re.compile(str(pattern)) for pattern in redaction["secret_value_patterns"]
            ),
            pii_patterns=tuple(
                re.compile(str(pattern)) for pattern in redaction["pii_value_patterns"]
            ),
            prohibited_classifications=frozenset(blocking["prohibited_classifications"]),
        )


class EgressGate:
    def __init__(self, policy: EgressPolicy) -> None:
        self.policy = policy

    @classmethod
    def from_yaml(cls, path: Path) -> "EgressGate":
        return cls(EgressPolicy.from_yaml(path))

    def evaluate(self, payload: Any) -> EgressDecision:
        original_bytes = _payload_size(payload)
        classifications = find_prohibited_classifications(
            payload,
            sensitive_key_patterns=self.policy.sensitive_key_patterns,
            secret_patterns=self.policy.secret_patterns,
        )
        prohibited = classifications & self.policy.prohibited_classifications
        if prohibited:
            return EgressDecision(
                allowed=False,
                payload=None,
                classifications=frozenset(classifications),
                redaction_count=0,
                truncated=False,
                original_bytes=original_bytes,
                output_bytes=0,
                reason="prohibited_classification",
            )

        stats = SanitizationStats()
        sanitized = sanitize_copy(
            payload,
            pii_patterns=self.policy.pii_patterns,
            replacement=self.policy.replacement,
            max_list_items=self.policy.max_list_items,
            max_string_chars=self.policy.max_string_chars,
            max_depth=self.policy.max_depth,
            stats=stats,
        )
        if stats.redaction_count:
            classifications.add("pii")
        output_bytes = _payload_size(sanitized)
        if output_bytes > self.policy.max_payload_bytes:
            return EgressDecision(
                allowed=False,
                payload=None,
                classifications=frozenset(classifications | {"oversized"}),
                redaction_count=stats.redaction_count,
                truncated=stats.truncated,
                original_bytes=original_bytes,
                output_bytes=output_bytes,
                reason="payload_too_large",
            )
        return EgressDecision(
            allowed=True,
            payload=sanitized,
            classifications=frozenset(classifications),
            redaction_count=stats.redaction_count,
            truncated=stats.truncated,
            original_bytes=original_bytes,
            output_bytes=output_bytes,
        )

    def enforce(self, payload: Any, *, source: str, principal: Principal) -> dict[str, Any]:
        decision = self.evaluate(payload)
        source_classification = _classify_source(source)
        audit = {
            "source": source,
            "source_classification": source_classification,
            "subject_hash": hashlib.sha256(principal.isolation_key.encode()).hexdigest()[:16],
            "allowed": decision.allowed,
            "classifications": sorted(decision.classifications),
            "redaction_count": decision.redaction_count,
            "truncated": decision.truncated,
            "original_bytes": decision.original_bytes,
            "output_bytes": decision.output_bytes,
            "reason": decision.reason,
        }
        logger.info("egress_decision", extra={"egress": audit})
        if not decision.allowed:
            raise EgressBlockedError(
                f"Enterprise data was blocked by egress policy ({decision.reason})."
            )
        return {
            "data": decision.payload,
            "_egress": {
                "source_classification": source_classification,
                "classifications": sorted(decision.classifications),
                "redactions": decision.redaction_count,
                "truncated": decision.truncated,
            },
        }


def _payload_size(value: Any) -> int:
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = repr(value)
    return len(serialized.encode("utf-8"))


def _classify_source(source: str) -> str:
    normalized = source.casefold()
    if normalized.startswith("workiq:"):
        return "enterprise_workiq"
    if normalized.startswith("fabric:"):
        return "enterprise_fabric"
    if normalized == "user_input":
        return "user_supplied"
    return "application_internal"
