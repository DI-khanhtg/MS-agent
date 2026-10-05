"""Backend-enforced read-only Work IQ path and operation policy."""

from urllib.parse import parse_qsl, unquote, urlsplit

ALLOWED_TOOLS = frozenset({"fetch", "get_schema", "search_paths"})
ALLOWED_WRITE_TOOLS = frozenset({"create_entity", "update_entity", "do_action"})
ALLOWED_PATH_PREFIXES = ("/me/", "/users/", "/sites/")
BLOCKED_PATH_SEGMENTS = ("/authentication/", "/serviceprincipals/")
BLOCKED_QUERY_PARAMETERS = frozenset({"$skip", "$skiptoken"})
MAX_ENTITY_URLS = 10
MAX_TOP = 25


class WorkIQPolicyError(ValueError):
    """A Work IQ call violates the application read-only policy."""


def validate_tool_name(name: str) -> None:
    if name not in ALLOWED_TOOLS:
        raise WorkIQPolicyError(f"Work IQ tool '{name}' is not enabled by read-only policy")


def validate_write_tool_name(name: str) -> None:
    if name not in ALLOWED_WRITE_TOOLS:
        raise WorkIQPolicyError(f"Work IQ tool '{name}' is not enabled by write policy")


def validate_entity_urls(entity_urls: list[str]) -> list[str]:
    if not entity_urls or len(entity_urls) > MAX_ENTITY_URLS:
        raise WorkIQPolicyError(f"entity_urls must contain between 1 and {MAX_ENTITY_URLS} paths")
    return [validate_read_path(path) for path in entity_urls]


def validate_read_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise WorkIQPolicyError("Work IQ paths must be relative and start with '/'")
    split = urlsplit(path)
    if split.scheme or split.netloc or split.fragment:
        raise WorkIQPolicyError("Absolute URLs and fragments are not allowed")
    decoded_path = unquote(split.path)
    lowered = f"{decoded_path.casefold().rstrip('/')}/"
    if ".." in decoded_path.split("/"):
        raise WorkIQPolicyError("Path traversal is not allowed")
    if not lowered.startswith(ALLOWED_PATH_PREFIXES):
        raise WorkIQPolicyError("The Work IQ resource path is not allowlisted")
    if any(segment in lowered for segment in BLOCKED_PATH_SEGMENTS):
        raise WorkIQPolicyError("The Work IQ resource path is blocked")

    query = dict(parse_qsl(split.query, keep_blank_values=True))
    if BLOCKED_QUERY_PARAMETERS & {key.casefold() for key in query}:
        raise WorkIQPolicyError("Pagination skip parameters are blocked")
    top = query.get("$top")
    if top is not None:
        try:
            top_value = int(top)
        except ValueError as exc:
            raise WorkIQPolicyError("$top must be an integer") from exc
        if not 1 <= top_value <= MAX_TOP:
            raise WorkIQPolicyError(f"$top must be between 1 and {MAX_TOP}")
    return path


def validate_schema_request(path: str, operation_type: str) -> tuple[str, str]:
    if operation_type.casefold() != "fetch":
        raise WorkIQPolicyError("Only fetch schemas are allowed")
    return validate_read_path(path), "fetch"


def validate_path_filter(path_filter: str) -> str:
    normalized = path_filter.strip()
    if not normalized or len(normalized) > 200:
        raise WorkIQPolicyError("filter must contain between 1 and 200 characters")
    return normalized
