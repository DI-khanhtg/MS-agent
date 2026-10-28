"""Download links derived from completed file writes, independent of model prose."""

import re
from uuid import UUID

from enterprise_agent.artifacts.models import ArtifactFormat
from enterprise_agent.tools.recording import get_completed_artifacts


def missing_artifact_links(answer: str = "") -> str:
    links = []
    seen = set()
    visible, open_fence = _visible_markdown(answer)
    for record in get_completed_artifacts():
        # File identity and format come from the validated local write receipt.
        artifact_id = str(UUID(record["artifact_id"]))
        kind = ArtifactFormat(record["artifact_type"])
        url = f"/api/artifacts/{artifact_id}/download"
        clickable = re.search(r"(?<![!\\])\[[^\]\n]+\]\(\s*" + re.escape(url) + r"\s*\)", visible)
        if artifact_id in seen or clickable:
            continue
        seen.add(artifact_id)
        links.append(f"- [Tải {kind.value.upper()}]({url})")
    if not links:
        return ""
    prefix = "\n\n" if answer.strip() else ""
    if open_fence:
        prefix += open_fence + "\n\n"
    return prefix + "Tài liệu đã tạo:\n\n" + "\n".join(links)


def _visible_markdown(answer: str) -> tuple[str, str]:
    """Ignore code/quoted content and retain the closing marker for truncated fences."""
    visible = []
    open_fence = ""
    for line in answer.splitlines():
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if open_fence:
            if (
                fence
                and fence[1][0] == open_fence[0]
                and len(fence[1]) >= len(open_fence)
                and not fence[2].strip()
            ):
                open_fence = ""
            continue
        if fence and (fence[1][0] == "~" or "`" not in fence[2]):
            open_fence = fence[1]
            continue
        if line.startswith((" ", "\t", ">")):
            continue
        visible.append(line)
    text = re.sub(r"(?s)(?<!\\)(`+)(?!`).*?\1(?!`)", "", "\n".join(visible))
    return text, open_fence
