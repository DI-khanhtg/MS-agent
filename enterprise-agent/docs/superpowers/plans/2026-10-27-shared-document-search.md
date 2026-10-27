# Shared document search and bounded reads

> **For agentic workers:** Use superpowers:executing-plans to implement and verify this plan.

**Goal:** Find documents shared with the signed-in user without crawling every folder, and avoid repeating downloads, parsing and retrieval within a chat turn.

**Architecture:** Add a read-only `workiq_search_documents(query, limit=5)` adapter to Work IQ `retrieve`, using `grounding` and `OneDriveAndSharePoint`. Discover and validate the live tool schema before calling it. Project structured retrieval hits into bounded references and grounding text, preserving security labels and explicit incomplete coverage. Cache successful retrieval and extracted documents only inside `turn_budget`, keyed by verified tenant, user and authentication assertion. Check egress on every returned result.

**Tech stack:** Existing Python/MCP/Agent Framework, JSON Schema validation (already required by MCP), existing offline document parsers. No OCR, vector database, background indexing, global document cache or new Graph permission grant.

## Design decisions

- Selected indexed search over recursive root/folder crawling because shared files can belong to other drives and the current failed searches cannot establish absence. A local full-text index would duplicate sensitive documents and require synchronization and revocation handling. Do not use the retiring `sharedWithMe` endpoint.
- Search narrows sources, makes one retrieval request, does not auto-retry potentially billed retrieval, and caps model-visible results to 1–10 hits. This reduces unnecessary work but is not a measured cost or latency guarantee; Work IQ billing/tenant availability remain prerequisites.
- Search is not a full scan. Empty results, partial/error terminal states, missing permissions, billing and missing tool availability must remain distinct from a claim that a file does not exist.
- Preserve the existing 3,500-character document read by default; allow an explicit bounded 14,000-character window split into strings within egress limits. Fetch and extract once per turn, including consecutive offsets, and release cache on exit/cancellation. Never share caches between turns, accounts or tenants.
- Use current workspace/branch so the running local project can be tested; no deployment or credentials changes. User requested immediate implementation and delegated optimization choices.

## Implementation and verification

- [x] Add failing tests for indexed discovery of another user's drive, result limits/egress, errors/empty/partial responses and no repeated retrieval.
- [x] Implement the search adapter, read-only registry/policy and runtime schema discovery; run focused tests.
- [x] Add failing tests for same-turn download/extraction reuse, larger windows, different identities/assertions and cache cleanup between turns/cancellation.
- [x] Implement bounded request-local caching and document windows; run focused tests.
- [x] Update agent workflows, operator documentation and a deterministic tool-loop integration test for shared PPTX → grounded Word artifact.
- [x] Run focused and full offline checks, review the changes, and record results below.

## Review focus

- Retrieval may be unavailable, schema-incompatible, denied or billable even when personal file listings work.
- Retrieval results and Markdown may contain sensitive labels, PII or prompt injection; filtering must precede any external model use.
- Hits may lack drive IDs or extensions; never synthesize IDs or claim a full document was read from an excerpt.
- Cache ownership must include tenant, user and assertion, and lifetime must end on failure/cancellation.
- Large/partial results must expose limits and coverage rather than silently claiming exhaustive discovery.

## Execution ledger

- Initial evidence: latest failing session successfully listed `/me/drive/root/children`; catalog discovery, child listing and a guessed search failed. No successful shared-document discovery or read was recorded.
- Official contract verified 2026-10-27: https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/retrieve (public preview; delegated access; Copilot Credits; live `tools/list` is schema authority).
- RED/GREEN cycles: missing search/registry/limits/cache (16 failures →51 pass), live schema/retrieval replay (5 failures →10 pass), search cost cap and omitted sensitivity label (2 failures →green), Unicode byte limits, PII expansion, cancellation cleanup and opaque numeric references (each observed failing before correction).
- Ruling: use per-turn opaque document references, in a separate 32-entry reference map, because egress masks long numeric drive/item IDs. Missing title extensions trigger one cached `$select=name` metadata read; actual persisted fetch wrapper and official tool reference confirm `structuredContent.results[].data`/`statusCode`. Handles never cross turns, identities or assertions.
- Ruling: offsets index whole-document sanitized text, explicitly reported as `offset_basis=sanitized_text`, because redaction after raw splitting both expands and leaks PII across boundaries. Classify the complete original first; audit pre-chunk redactions. Original extraction limits stay unchanged; maximum accepted sanitized offset is1000000.
- Independent review found and verified fixes for post-redaction text loss and cache retention in cancelled child-task contexts. Cache now clears and closes explicitly; late child loads cannot repopulate it.
- The deterministic full tool loop exposed an existing artifact argument bug (`dict` instead of `ArtifactSpec`); validation at the tool boundary now fixes shared PPTX → actual DOCX output.
- Minor review note: bounded/deduplicated search metadata may omit grounding inline citation IDs. Agent must cite returned source URLs, not invent mappings; no claim of exhaustive retrieval is permitted.
- Focused final adapter/runtime checks:111 passed. Final full offline verification:516 passed,6 skipped,7 live deselected; one upstream Starlette/AnyIO deprecation warning. Skips cover unavailable Playwright/bundled artifact-tool and a POSIX-only test. Ruff, format check on the13 edited Python files, lockfile and diff checks passed. Localhost health responds with auth and Work IQ configured. No live provider calls or measured billing/latency claims.
