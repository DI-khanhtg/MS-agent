"""Validate Microsoft sharing URLs and keep their tokens out of model-facing input."""

import base64
import re
from urllib.parse import unquote, urlsplit

from enterprise_agent.agent.budget import register_source_reference, resolve_source_reference

LINK_NAMESPACE = "workiq_shared_url"
SHARED_ITEM_SELECT = "id,name,webUrl,parentReference,file,folder,remoteItem,size"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.sharepoint\.com")
_URL = re.compile(r"https://[^\s<>\"`]+", re.IGNORECASE)


class SharedLinkError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_shared_url(url: str) -> str:
    message = (
        "Use a complete HTTPS organizational SharePoint/OneDrive link, "
        "without masked IDs or credentials."
    )
    if not isinstance(url, str) or not 1 <= len(url.strip()) <= 4096:
        raise SharedLinkError("invalid_shared_link", message)
    url = url.strip()
    decoded = unquote(unquote(url))
    try:
        parsed = urlsplit(url)
        invalid = (
            parsed.scheme.casefold() != "https"
            or not (_HOST.fullmatch(parsed.hostname or "") or parsed.hostname == "1drv.ms")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path in ("", "/")
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in url
            )
            or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
            or "\\" in decoded
            or any(part in (".", "..") for part in unquote(unquote(parsed.path)).split("/"))
            or "[redacted" in decoded.casefold()
            or "[truncated" in decoded.casefold()
        )
    except ValueError:
        raise SharedLinkError("invalid_shared_link", message) from None
    if invalid:
        raise SharedLinkError("invalid_shared_link", message)
    # Fragment identifiers are browser-only (for example a PDF page number).
    return parsed._replace(fragment="").geturl()


def shared_item_path(url: str) -> str:
    validated = validate_shared_url(url)
    token = base64.urlsafe_b64encode(validated.encode("utf-8")).decode().rstrip("=")
    return f"/shares/u!{token}/driveItem"


def original_shared_url(link_id: str) -> str | None:
    return resolve_source_reference(LINK_NAMESPACE, link_id)


def protect_shared_links(message: str) -> str:
    """Replace valid URLs with current-turn, owner/assertion-scoped opaque references."""
    registered = 0

    def replace(match: re.Match) -> str:
        nonlocal registered
        value = match.group(0)
        url = value.rstrip(".,;)]}")
        try:
            validated = validate_shared_url(url)
        except SharedLinkError:
            return value
        if registered >= 8:
            return "Microsoft 365 link (link limit reached; process at most eight links per turn)"
        reference = register_source_reference(LINK_NAMESPACE, (validated,), validated)
        if reference is None:
            return "Microsoft 365 link (reference unavailable; resend in an active turn)"
        registered += 1
        return f"Microsoft 365 link (link_id={reference})" + value[len(url) :]

    return _URL.sub(replace, message)
