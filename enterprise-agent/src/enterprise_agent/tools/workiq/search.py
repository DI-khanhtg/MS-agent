"""Bounded projection of indexed Work IQ document search; no directory crawling."""

import re
from typing import Any

from enterprise_agent.tools.workiq.policy import WorkIQPolicyError, validate_document_path

RETRIEVAL_MEDIA_TYPE = "application/vnd.ms-workiq.retrieval"


class DocumentSearchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_search(query: str, limit: int) -> str:
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 1000
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 10
    ):
        raise DocumentSearchError(
            "invalid_search", "Use a specific query of 1–1000 characters and a limit of 1–10."
        )
    return " ".join(query.split())


def search_security_payload(result: dict) -> dict:
    """Map explicit sensitivity label names into the existing classification policy."""
    classifications = []

    def visit(value):
        if isinstance(value, dict):
            label = value.get("sensitivityLabel")
            if isinstance(label, dict) and isinstance(label.get("displayName"), str):
                classifications.append(
                    {
                        "classification": re.sub(
                            r"[\s-]+", "_", label["displayName"].strip().casefold()
                        )
                    }
                )
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return {"result": result, "labels": classifications}


def project_search(result: dict[str, Any]) -> dict:
    payload = result.get("structuredContent", {})
    payload = payload.get(RETRIEVAL_MEDIA_TYPE) if isinstance(payload, dict) else None
    if result.get("isError") or not isinstance(payload, dict):
        raise DocumentSearchError(
            "invalid_search_response", "Work IQ did not return a valid indexed retrieval response."
        )
    if payload.get("error") or payload.get("stoppedReason") != "completed":
        raise DocumentSearchError(
            "search_incomplete",
            "Indexed search did not complete. Do not infer that the file is absent or that "
            "the user lacks access. Check Work IQ access, consent, billing and service status.",
        )
    hits = payload.get("retrievalHits")
    markdown = payload.get("markdown", "")
    count = payload.get("resultCount")
    if (
        not isinstance(hits, list)
        or not all(isinstance(hit, dict) for hit in hits)
        or not isinstance(markdown, str)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < len(hits)
    ):
        raise DocumentSearchError(
            "invalid_search_response", "Work IQ returned invalid search metadata."
        )
    documents = []
    seen = set()
    for hit in hits:
        # Structured identifiers, not Markdown parsing or guessed owners' item IDs.
        path = hit.get("entityUrl")
        if not path and isinstance(hit.get("driveId"), str) and isinstance(hit.get("itemId"), str):
            path = f"/drives/{hit['driveId']}/items/{hit['itemId']}"
        try:
            resolved = validate_document_path(path) if isinstance(path, str) else None
        except WorkIQPolicyError:
            resolved = None
        document = {
            field: hit[field] for field in ("id", "webUrl", "sensitivityLabel") if field in hit
        }
        metadata = hit.get("resourceMetadata")
        metadata = metadata.get("document") if isinstance(metadata, dict) else None
        if isinstance(metadata, dict):
            document["resourceMetadata"] = {
                "document": {
                    field: metadata[field][:1000]
                    for field in ("title", "authorName", "occurrenceTime")
                    if isinstance(metadata.get(field), str)
                }
            }
        if resolved:
            document["document_path"] = resolved.removesuffix("/content")
        identity = resolved or hit.get("webUrl") or hit.get("id")
        if isinstance(identity, str) and identity in seen:
            continue
        if isinstance(identity, str):
            seen.add(identity)
        documents.append(document)
        if len(documents) == 10:
            break
    return {
        "source": "workiq:document_search",
        "documents": documents,
        "grounding": markdown[:3500],
        "result_count": count,
        "stopped_reason": "completed",
        "exhaustive": False,
        "truncated": len(documents) < len(hits) or len(markdown) > 3500 or count > len(hits),
        "coverage": "Indexed matches only; not an inventory or a complete document read.",
        "trust": "untrusted_document_data",
    }
