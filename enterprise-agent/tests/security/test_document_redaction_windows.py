import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.tools.local.documents import LocalDocumentTools


@pytest.mark.parametrize(
    "content,max_chars",
    [(b"a@b.co " * 12000, 100_000), (b"x" * 150_000, 200_000)],
    ids=["redaction-expansion", "configured-larger-text"],
)
def test_local_document_next_offset_is_readable_after_redaction_or_larger_limits(
    tmp_path, content, max_chars
):
    settings = Settings(_env_file=None, local_documents_root=tmp_path, document_max_chars=max_chars)
    (tmp_path / "source.txt").write_bytes(content)
    reader = LocalDocumentTools(settings, EgressGate.from_yaml(settings.egress_policy_path))
    first = reader.read("source.txt", offset=98000)
    assert first["next_offset"] == 101500
    second = reader.read("source.txt", offset=first["next_offset"])
    assert second["text"]
    assert "a@b.co" not in first["text"] + second["text"]
