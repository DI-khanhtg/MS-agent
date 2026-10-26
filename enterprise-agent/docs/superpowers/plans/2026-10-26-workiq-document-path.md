# Repair OneDrive document reads

The persisted failed turn passed `/me/drive/items/{id}` to the document reader.
The tool rejected it before `fetch_blob` because `/content` was absent. The agent
framework reduced the exception to `Error: Function failed.`, hiding the cause.

1. Reproduce the observed drive-item path with a real PDF fixture.
2. Normalize resolved drive-item paths to `/content`, retaining read-only guards.
3. Return safe, actionable document errors to the agent without raw upstream details.
4. Verify PDF extraction and error delivery through the real framework tool loop,
   then run the relevant regression suite and lint.

Live Microsoft 365 extraction still requires the browser user's delegated session;
offline fixtures cannot establish that a specific live file is readable.

## Validation

- Reproduced the missing `/content` failure before the fix with a genuine PDF fixture.
- Verified normalized paths, exact opaque IDs, masked filenames retaining `.pdf`, and
  rejection of unsafe/collection/masked-ID paths without token acquisition or network calls.
- The real framework loop forwards PDF text and safe path/access/extraction error codes
  to the next model request, rather than `Function failed`.
- Source timeouts still cover token acquisition and network reads, retain timeout audit
  events, and now deliver a safe diagnostic through the egress gate.
- `uv run --with playwright pytest -q --tb=short`: 405 passed, 9 skipped;
  skipped checks require external live credentials or the optional artifact runtime.
- Ruff, formatting of edited Python files, lock consistency and diff whitespace checks pass.
- Localhost `/health` reports `ok`; the existing server uses automatic reload.
- Independent reviewer could not run because its selected model was at capacity;
  implementation and regression cases were reviewed inline.
