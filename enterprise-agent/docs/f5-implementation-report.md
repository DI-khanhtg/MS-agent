# F5 implementation report

## Outcome

The shared document reader now routes DOC/PPT/XLS/RTF/ODT through headless LibreOffice/UNO
and reuses the existing DOCX/PPTX/XLSX parsers. EML uses stdlib email; MSG uses extract-msg.
All seven new formats require verified container isolation. The default remains compatible
with existing native formats while the sandbox is disabled. Enabling it defaults to
isolating native formats as well. No host converter or inline advanced-parser fallback is
available through application configuration.

The before/after support matrix, limits, dependency justification, build/install instructions
and runtime probe are in [advanced-documents.md](advanced-documents.md).

## F5 file changes

Paths are relative to `enterprise-agent/`. Earlier uncommitted F3 changes were preserved.
F5 did not modify other applications or change model/provider/OBO/approval policies.

| Area | Modified or added files |
| --- | --- |
| Existing reader | `src/enterprise_agent/tools/document_reader.py` |
| Routing / preflight | `src/enterprise_agent/tools/document_formats.py`, `src/enterprise_agent/tools/document_validation.py` |
| Email / conversion | `src/enterprise_agent/tools/document_email.py`, `src/enterprise_agent/tools/document_conversion.py` |
| Isolation / protocol | `src/enterprise_agent/tools/document_sandbox.py`, `src/enterprise_agent/tools/document_worker.py` |
| Local / Microsoft workflows | `src/enterprise_agent/tools/local/documents.py`, `src/enterprise_agent/tools/workiq/tools.py` |
| API / upload UI / configuration | `src/enterprise_agent/api/local_documents.py`, `src/enterprise_agent/api/app.py`, `src/enterprise_agent/ui/app.js`, `src/enterprise_agent/config.py` |
| Dependencies / installation | `pyproject.toml`, `uv.lock`, `Dockerfile.documents`, `scripts/check_document_sandbox.py` |
| Unit tests | `tests/unit/test_document_formats.py`, `tests/unit/test_document_email.py`, `tests/unit/test_document_corpus.py`; updated native-reader expectations and the extraction spy in `tests/unit/test_document_reader.py` / `tests/unit/test_workiq_tools.py` |
| Security tests | `tests/security/test_document_sandbox.py`, `tests/security/test_document_archives.py`, `tests/security/test_document_redaction_windows.py` |
| Integration tests | `tests/integration/test_advanced_documents.py`, `tests/integration/test_document_sandbox_real.py` |
| Fixtures | `tests/fixtures/document_corpus.py`, `tests/fixtures/legacy/{simple.doc,basic_test.ppt,simple.xls}`, their reference text, README, Apache POI LICENSE/NOTICE |
| Documentation | `README.md`, `ARCHITECTURE.md`, `docs/advanced-documents.md`, `docs/superpowers/plans/2026-10-30-f5-document-processing.md`, this report |

## Validation and accuracy

The final offline suite passed **813 tests**, with **33 skipped** and **8 deselected**, in
142.23 seconds. The skips include 29 explicit real-container cases and four existing host/
optional-runtime cases. The focused F5/native regression selection passed 140 tests before
two additional MIME/crash regressions were added; those also passed. A subsequent check of
all 92 exported locked test dependencies required no changes, and 37 email/corpus/integration
tests passed against that environment. Ruff, diff and offline lock consistency checks passed.

The default corpus measures real native/email parsers against 22 independent, self-authored
references. Minimum and mean whitespace-normalized character agreement were **100%** with
`SequenceMatcher(autojunk=False).ratio()`; each test requires at least 95%. Fixtures cover
all eight native extensions plus plain/HTML/Unicode EML and MSG bodies. This measurement
does not use a mocked extraction result and does not claim arbitrary-file fidelity.

Four generated RTF/ODT fixtures plus three genuine Apache POI DOC/PPT/XLS files extend the
opt-in real-container corpus to 29 fixtures across all 15 supported extensions. Default
tests verify their type checks and converter routing/native-parser reuse with explicit
test doubles. **Real LibreOffice accuracy and runtime kernel enforcement were not executed**
because this Windows host has no Docker/Podman or LibreOffice installation. The real suite
is opt-in and must pass against the deployed image before enabling advanced ingestion.

Security tests exercise macro types/streams, mismatch/oversize/malformed content, forged ZIP
counts rejected before ZipInfo allocation, archive expansion, XML entities/event scripts,
MIME/HTML nesting, active HTML, parser failures, kernel/inspect policy refusal, no source
bytes before valid proof, blocked-stdin watchdog termination, cleanup failure, full-field
redaction, continuation after redaction expansion, handles and cross-user isolation.
Existing PDF/DOCX/PPTX/XLSX, search/Work IQ and artifact/F3 tests remain in the offline suite.

Independent review identified an encoding bypass in raw-byte XML security checks and a
pagination bound incompatible with redaction expansion. Both were reproduced with failing
tests, fixed, and verified with passing regressions. XML safety now uses decoding-aware
Expat callbacks and forbids declarations before entity expansion; local offsets index the
sanitized result up to the existing one-million-character boundary.

## Commands

From `enterprise-agent/`:

```powershell
uv sync --extra dev --extra documents
uv run --extra dev pytest tests/unit tests/integration tests/security evals -q -m 'not live'
uv run --extra dev ruff check src tests scripts
uv lock --check --offline
```

This session used the existing `.venv/Scripts/python.exe` for verification. `uv sync` could
not replace the Windows CLI executable while it was running; the new parser dependencies
were installed with `uv pip install` without terminating the running service. Stop that
service before the next full sync. No default tests call live Microsoft or LLM services.

The optional upload browser test was skipped because Playwright is not installed. Other
known skips include the unavailable optional Node artifact renderer, POSIX-only restart
semantics, and filesystem-link creation privileges on this Windows host. The installed
Starlette test client emits an AnyIO deprecation warning.

## Remaining deployment assumptions

- Build and pin `Dockerfile.documents`, provision a supported Linux/cgroup-v2 launcher, run
  `scripts/check_document_sandbox.py`, then run all 29 real-container accuracy cases. The
  current native-only default deliberately does not claim OS isolation for existing parsers.
- Verify rootless resource delegation and actual Docker inspect fields/cgroup mounts on the
  target environment. Requested CLI flags alone are never accepted as isolation evidence.
- The existing backend image does not contain a Docker client/daemon. Launcher access and
  aggregate worker capacity must be provisioned explicitly; no worker gets daemon access.
- Monitor cleanup failures and confirmed abandoned labelled containers after daemon/host
  failures. Encrypted host/VM swap, snapshots and physical erasure remain infrastructure
  responsibilities; transient tmpfs does not replace those controls.
- Text-only extraction cannot recover scans or images, and legacy conversion can lose
  unsupported layout/features. Attachments and encrypted/macro-enabled files are rejected
  or excluded as documented. No source Microsoft documents are modified.
