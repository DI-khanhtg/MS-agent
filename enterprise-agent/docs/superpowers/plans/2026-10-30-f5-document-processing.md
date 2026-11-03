# F5 Advanced Document Processing Implementation Plan

**Goal:** Extend the existing extraction pipeline with seven formats and fail-closed isolation.
**Architecture:** Keep `tools/document_reader.py` and its native Office/PDF extractors. Add a
format registry and bounded content validation. Advanced formats always run in a disposable
Linux Docker container, using LibreOffice/UNO with macros and link updates disabled, or
stdlib email/extract-msg. Check Docker policy before starting and kernel enforcement before
sending content. Host transport uses bounded stdin/stdout; no document-bearing host mounts.
**Constraints:** Preserve F3 changes, identity/handles, egress, providers and approval behavior.
Only `enterprise-agent/` may change. Default tests are offline. No host converter fallback.

## Current formats

TXT/MD/CSV/JSON: UTF-8 text. DOCX: paragraphs/tables/header/footer. PPTX: text/tables/groups/
notes, 200 slides. XLSX: cached values, no calculation. PDF: selectable text, 200 pages,
no OCR. DOC/PPT/XLS/RTF/ODT/MSG/EML currently unsupported. There is no parser registry.
Source limits are 5 MB (explicit cloud reads up to 20 MB), expanded Office ZIPs 50 MB,
text 100,000 characters, structure depth 32. Browser upload is at most 2 MB/file.
This Windows host has no Docker/Podman or LibreOffice installed; real container validation
must be reported separately from offline parser/transport-contract tests.

## Files and steps

- [x] Write failing tests for format validation, MIME/extension mismatch, email metadata/HTML,
  sandbox policy/attestation, bounded transport, timeout/crash cleanup, corpus agreement,
  local/Work IQ isolation and native-format regressions.
- [x] Add `tools/document_formats.py`, `tools/document_validation.py`,
  `tools/document_email.py`, `tools/document_sandbox.py`, `tools/document_worker.py` and
  `tools/document_conversion.py`; extend `tools/document_reader.py` in place.
- [x] Extend `config.py`, `api/local_documents.py`, `api/app.py`,
  `tools/local/documents.py`, and `tools/workiq/tools.py` to pass settings and preserve
  existing handles, whole-result privacy checks and untrusted result labeling.
- [x] Add justified optional `documents` dependencies, a converter Dockerfile, a probe/
  operational check, versioned fixtures/reference text and offline/default tests. Add an
  explicit opt-in container test for actual conversion/enforcement; never substitute a
  mocked conversion result for measured real-converter accuracy.
- [x] Update README, architecture, support matrix and operations documentation. Run focused
  tests, existing offline suite, Ruff and lock consistency checks; review boundary failures.

## Security and review focus

Reject macros, wrong container/stream types, hostile archives and excessive nesting before
conversion. Never execute email HTML or extract/save attachments. No host paths or credentials
enter the sandbox. Require a pinned image, nonroot identity, no network, no capabilities,
seccomp/no-new-privileges, readonly root, private tmpfs, cgroup-v2 CPU/memory/PID enforcement
and process limits. A watchdog must remove the container even when the CLI/parser hangs.
Do not return content until cleanup succeeds. Keep errors fixed and content-free. All
extracted fields remain tool data and pass the existing egress gate. Disabled/unavailable
sandbox rejects advanced formats without breaking existing native-format defaults.

## Verification and review

Independent review reproduced two important boundary failures: UTF-16 XML bypassed raw-byte
security expressions, and local sanitized pagination exceeded its old 100,000 offset bound.
Failing regressions were added before fixes. Encoding-aware Expat declaration/element handlers
and sanitized-offset bounds fixed the failures; regression tests passed. Transport tests also
verify proof-before-input, actual subprocess termination on stalled stdin and cleanup failure.

Final offline suite: 813 passed, 33 skipped, 8 deselected in 142.23 seconds. The 29 real-container
accuracy/enforcement cases remain explicitly unverified because no Docker/LibreOffice runtime
exists on this Windows host. The measured 22 native/email fixtures achieved 100% minimum and
mean normalized character agreement. Ruff, locked dependency consistency and diff checks passed.
An optional browser upload check was skipped because Playwright is unavailable. No default
test called live Microsoft or LLM services. F3 changes and other applications were preserved.
