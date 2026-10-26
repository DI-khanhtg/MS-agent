# Reading PDF and Office documents

**Goal:** Let the agent read common PDF/Word/PowerPoint/Excel files from selected local
files/folders and Microsoft365, including a dedicated document attachment action in chat.

**Architecture:** Share one bounded text extractor between local imports, local tools and
Work IQ fetch_blob. Support PDF, DOCX, PPTX, XLSX and existing UTF8 text formats. Extract
PowerPoint grouped text/tables/speaker notes and preserve useful page/slide/section labels.
Report format, page/slide/sheet count and text-only coverage warnings so images, scans and
unsupported content are not silently treated as read. Retain chunking, account isolation,
source references and egress checks. Use the existing Python libraries; legacy binary
DOC/PPT/XLS conversion and OCR are separate capabilities, not implicit guarantees.

**Interfaces:** `tools/document_reader.py` exports SUPPORTED, DocumentExtractionError,
ExtractedDocument(text, format, unit_count, warnings) with metadata property, plus
extract_document(content,suffix,max_chars=100000) and extract_text compatibility wrapper.
LocalDocumentError remains available as an alias. Public API config supplies allowed
extensions to the frontend to avoid inconsistent lists. The attachment picker shares the
existing account-scoped import endpoint and passes the selected import folder with chat.

- [x] Add genuine in-memory file fixtures and failing extractor/adapter/UI tests.
- [x] Implement shared PDF/Office reader and extraction-coverage metadata.
- [x] Wire local/Work IQ reads, document picker and supported-format hints.
- [x] Update model workflow guidance and user docs with format/size/OCR limits.
- [x] Run full offline/browser suite, review changes and report remaining limits.

**Constraints:** Preserve the current working tree and existing login/import protections.
Never fetch arbitrary download URLs or execute document macros. Read limits remain5MB
per source,50MB expanded Office ZIP,100000 extracted characters; local browser imports
retain2MB per-file limit and bounded batching. Work in the existing checkout.

## Validation and handoff

- Full `uv run --with playwright pytest -q --tb=short`: **379 passed, 9 skipped**;
  one existing Starlette/AnyIO deprecation warning. Seven live/tenant checks and two
  optional artifact-tool renderer checks were skipped.
- Fifty focused reader tests cover real generated PDF/DOCX/PPTX/XLSX, grouped shapes,
  tables, speaker notes, headers/footers, image-only/empty units, encryption/corruption,
  text/source/ZIP/page/depth bounds and formula cache limitations.
- Local import and Work IQ adapter tests verify all four Office/PDF formats, metadata,
  source references, account identity and egress behavior. Blob error/size mismatch
  responses are rejected. Chrome verifies four simultaneous document attachments,
  selected-folder context and cleanup at sign-out.
- Ruff, lockfile and git whitespace checks pass. Shared extractor and integration code
  were reviewed manually; the attempted independent reviewer was unavailable.
- The user explicitly selected selectable-text PDF first. OCR and visual/chart analysis
  remain outside this delivery. DOC/PPT/XLS conversion is documented. No live Microsoft365
  file retrieval or paid model call was needed for validation.
- Usage and limits are in `docs/DOCUMENT_READING.md`. Existing dependencies suffice;
  no dependency or credential changes were required.
