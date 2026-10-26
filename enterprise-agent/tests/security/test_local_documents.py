import io
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from openpyxl import load_workbook

from enterprise_agent.artifacts.models import ArtifactSpec
from enterprise_agent.artifacts.office import XlsxRenderer
from enterprise_agent.artifacts.templates import CompanyTemplate
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.local.documents import LocalDocumentError, LocalDocumentTools


def tools(tmp_path, **overrides):
    settings = Settings(_env_file=None, local_documents_root=tmp_path, **overrides)
    return LocalDocumentTools(settings, EgressGate.from_yaml(settings.egress_policy_path))


@contextmanager
def account(user):
    principal = Principal("tenant", user, user, None, frozenset(), None, "token")
    token = set_request_context(RequestContext(principal))
    try:
        yield
    finally:
        reset_request_context(token)


def test_authenticated_documents_are_isolated_from_other_accounts_and_shared_demo(tmp_path):
    (tmp_path / "shared.md").write_text("demo")
    reader = tools(tmp_path, auth_enabled=True)
    with account("alice"):
        assert reader.root != tmp_path
        reader.root.mkdir(parents=True)
        (reader.root / "private.md").write_text("Alice")
        assert reader.read("private.md")["text"] == "Alice"
        assert [item["path"] for item in reader.manifest()["files"]] == ["private.md"]
    with account("bob"):
        with pytest.raises(LocalDocumentError):
            reader.read("private.md")
        with pytest.raises(LocalDocumentError):
            reader.read("shared.md")
    with pytest.raises(LocalDocumentError):
        reader.read("shared.md")
    demo = tools(tmp_path)
    assert demo.read("shared.md")["text"] == "demo"
    assert [item["path"] for item in demo.manifest()["files"]] == ["shared.md"]
    with pytest.raises(LocalDocumentError):
        demo.resolve("_users/anything/private.md")


@pytest.mark.parametrize(
    "path",
    [
        "a\\b.md",
        "C:secret.md",
        "a/./b.md",
        "a//b.md",
        "x:ads.md",
        "a /b.md",
        "CON.md",
        "a" * 256 + ".md",
        "/".join(["folder" * 30] * 8) + "/a.md",
    ],
)
def test_windows_ambiguous_paths_are_rejected(tmp_path, path):
    with pytest.raises(LocalDocumentError):
        tools(tmp_path).resolve(path)


@pytest.mark.parametrize("legacy_pathlib", [False, True])
def test_authenticated_namespace_rejects_links(tmp_path, monkeypatch, legacy_pathlib):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "_users").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Creating directory links is unavailable")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(tmp_path / "_users"), str(outside)],
            check=True,
            capture_output=True,
        )
    if legacy_pathlib and hasattr(Path, "is_junction"):
        monkeypatch.delattr(Path, "is_junction")
    with account("alice"), pytest.raises(LocalDocumentError, match="links"):
        tools(tmp_path).resolve("private.md")


def test_import_validates_entire_batch_before_writing_and_never_overwrites(tmp_path):
    reader = tools(tmp_path, auth_enabled=True, local_max_files=2, enabled_tool_groups="local_read")
    with account("alice"):
        with pytest.raises(LocalDocumentError):
            reader.import_documents([("run/a.md", b"valid"), ("run/.env", b"secret")])
        assert not reader.root.exists()
        assert reader.import_documents([("run/a.md", b"Alice")])["paths"] == ["run/a.md"]
        with pytest.raises(LocalDocumentError):
            reader.import_documents([("run/b.md", b"new"), ("run/a.md", b"overwrite")])
        assert not (reader.root / "run/b.md").exists()
        assert reader.read("run/a.md")["text"] == "Alice"
        reader.import_documents([("run/b.md", b"two")])
        with pytest.raises(LocalDocumentError, match="count"):
            reader.import_documents([("run/c.md", b"three")])
    with account("bob"):
        reader.import_documents([("run/a.md", b"Bob")])
        assert reader.read("run/a.md")["text"] == "Bob"


@pytest.mark.parametrize(
    "files",
    [
        [("large.md", b"a" * 1025)],
        [("bad.exe", b"a")],
        [("bad.pdf", b"not a PDF")],
        [("same.md", b"1"), ("SAME.md", b"2")],
        [("a.md", b"1"), ("a.md/b.md", b"2")],
        [],
    ],
)
def test_import_rejects_invalid_sizes_formats_duplicates_and_conflicts(tmp_path, files):
    reader = tools(
        tmp_path, auth_enabled=True, local_max_file_bytes=1024, enabled_tool_groups="local_read"
    )
    with account("alice"), pytest.raises(LocalDocumentError):
        reader.import_documents(files)
    assert not list(tmp_path.rglob("*.md"))


def test_anonymous_imports_cannot_modify_shared_demo(tmp_path):
    with pytest.raises(LocalDocumentError):
        tools(tmp_path).import_documents([("new.md", b"not allowed")])
    assert not (tmp_path / "new.md").exists()


def test_configured_root_keeps_existing_path_normalization(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    (root / "a.md").write_text("demo")
    assert tools(tmp_path / "nested" / ".." / "documents").read("a.md")["text"] == "demo"


@pytest.mark.parametrize("path", ["../.env", ".env", "sub/../../secret", "C:/Windows/win.ini"])
def test_local_paths_cannot_escape_or_read_secrets(tmp_path, path):
    with pytest.raises(LocalDocumentError):
        tools(tmp_path).resolve(path)


def test_inventory_excludes_secrets_and_preserves_pagination(tmp_path):
    (tmp_path / ".env").write_text("secret")
    for index in range(30):
        (tmp_path / f"file-{index:02}.md").write_text("test")
    reader = tools(tmp_path)
    first, second = reader.manifest(), reader.manifest(offset=25)
    assert len(first["files"]) == 25 and len(second["files"]) == 5
    assert first["next_offset"] == 25 and second["next_offset"] is None
    assert not any(item["path"] == ".env" for item in first["files"])
    assert reader.manifest()["coverage"].startswith("inventory_only")


def test_chunks_and_oversize_files_report_limits(tmp_path):
    (tmp_path / "long.md").write_text("a" * 7000)
    reader = tools(tmp_path)
    assert reader.read("long.md")["next_offset"] == 3500
    assert reader.read("long.md", 3500)["next_offset"] is None
    with pytest.raises(LocalDocumentError, match="size"):
        tools(tmp_path, local_max_file_bytes=1024).read("long.md")


def test_excel_text_is_not_executable_formula():
    spec = ArtifactSpec(
        artifact_type="xlsx",
        title="Report",
        tables=[{"columns": ["Input"], "rows": [['=HYPERLINK("https://evil.example")'], ["+1+1"]]}],
    )
    template = CompanyTemplate("default", "Enterprise", "#164E63", "#0EA5E9", "Calibri")
    result = XlsxRenderer().render(spec, template)
    sheet = load_workbook(io.BytesIO(result.content), data_only=False)["Table 1"]
    assert sheet["A2"].data_type == "s"
    assert sheet["A3"].data_type == "s"
