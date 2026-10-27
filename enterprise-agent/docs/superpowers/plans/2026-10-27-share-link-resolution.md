# Resolve shared document links and improve search selection

> **For agentic workers:** Use superpowers:executing-plans. User requested direct-link implementation first: Tasks1–2 and their direct-link tests/documentation are implemented. Search ranking and general search-budget UX (Tasks3–4) remain deferred. No Entra permission changes are authorized or performed by this code.

**Goal:** A user can supply a Microsoft 365 file sharing link and obtain a grounded summary/Word draft without depending on an indexed filename search, subject to their actual file and application permissions.

**Architecture:** Prefer a backend-only sharing-link resolver, then reuse owner-scoped document references, bounded extraction and artifact generation. If only a filename is available, rank all returned metadata locally before applying the model-visible result limit. Preserve separate search-query and time budgets and expose their actual outcomes.

**Tech stack:** Existing Python/MCP/Agent Framework, offline document parsers and DOCX renderer; Microsoft Graph/OBO only if the existing Work IQ deployment cannot resolve the link and the operator configures the required consent.

**Spec:** Requirements, evidence, approach and acceptance criteria are included below.

## Evidence and approach

- Latest persisted calls completed searches; one returned nine hits, five reached the model and none of those five titles matched the requested document. This does not establish whether a match was among omitted hits.
- Third searches returned `search_budget_exhausted`, not `search_timeout`. Budget currently resets per chat turn, not per entire conversation.
- The existing application policy has no sharing-link resolver. Work IQ retrieval URL scoping accepts canonical URLs, not sharing links.
- Prefer resolving an exact supplied link over another semantic search. Prefer ranking metadata over another Gemini selection pass. A full background index/copy or increasing timeouts alone does not address the observed failures.

## Constraints and review focus

- No arbitrary URL downloads, server-side browser scraping, or automatic redirects with bearer credentials. Resolve against trusted Microsoft API hosts, using validated organizational SharePoint/OneDrive links.
- Do not grant/redeem durable sharing permissions or mutate source files silently. Delegated authorization and tenant policy remain authoritative.
- Work IQ support for `/shares/.../driveItem` must be verified; Microsoft Graph's endpoint existing does not prove the Work IQ deployment permits it.
- Graph's documented least delegated permission for this endpoint is `Files.ReadWrite`. Existing Work IQ consent must not be assumed to be a Graph token or that scope. Any Graph fallback needs correctly scoped OBO, operator setup and applicable consent before use; code must not silently broaden permissions.
- Resolved IDs and reference maps remain scoped to verified tenant/user/assertion and current turn. Preserve full-result security checks and sensitivity labels. Do not log raw sharing tokens, bearer tokens or full provider errors.
- A missing result, query cap, timeout, unavailable resolver, denied access and expired link are distinct outcomes. Do not label a valid SharePoint link as an external unsupported file just because search failed.

## Task 1: Establish the supported authenticated resolution route

**Files:** `src/enterprise_agent/tools/workiq/client.py`, `policy.py`; add focused resolver contract tests under `tests/unit/`; update `docs/WORKIQ_SETUP.md` if extra setup is actually needed.

- [x] Verify the deployed exact read-only shared-item route with a successful authenticated read (see ledger ruling). No writes or indexed retrieval were used to probe the link.
- [x] Pin safe representative success/error envelopes in tests; separate route unavailable, authorization denied, missing item and timeout.
- [x] Use Work IQ resolution under current consent and a separate correctly scoped Graph adapter for large-file download. Never reuse a Work IQ access token as a Graph token or grant consent automatically.
- [x] Report the tenant/consent blocker separately from a software defect. No live permission change was performed.

## Task 2: Add direct sharing-link resolution and reading

**Files:** Create `src/enterprise_agent/tools/workiq/links.py` (provider-neutral URL validation/encoding); extend the selected adapter, `tools/registry.py`, Work IQ workflows and unit tests. Keep a Graph transport separate if needed.

**Interface:** A read-only `workiq_resolve_document_link(url)` (or explicitly named Graph equivalent if selected) returns a current-turn `document_id`, filename, canonical source URL and safe status. Existing `workiq_read_document(document_id=...)` remains the content reader.

- [x] Write failing tests for organizational SharePoint sharing URLs, encoded queries, unsupported hosts/protocols, URL userinfo, malicious paths, expired links, denied access, timeout and missing owner-drive metadata.
- [x] Encode a validated sharing URL as the Microsoft-required sharing token, resolve the file through the selected Microsoft API, validate returned drive/item IDs and register a private opaque reference. Do not manually infer IDs from a sharing URL.
- [x] Allow only the exact required shared-item read route; do not allow all `/shares/` operations or mutation headers.
- [x] Reuse bounded download/extraction, original filename resolution, whole-text egress filtering and the current-turn cache. Cache a resolved link within a turn without sharing it across identities or turns.
- [x] Prefer this route whenever the user supplies a link. It works independently of the indexed-search quota; actual file permissions remain necessary.

## Task 3: Rank search metadata before truncating it

**Files:** `src/enterprise_agent/tools/workiq/search.py`, `tools.py`, agent workflows; `tests/unit/test_workiq_tools.py` and focused ranking tests.

- [ ] Write a failing nine-hit fixture where the exact requested file is below the first five hits. Include duplicate hits, extensionless titles, mixed case, accents and misleading owner-only matches.
- [ ] Introduce a deterministic filename/title/topic ranking hint separate from the provider query. Normalize case, separators and common extension variations. Weight an exact filename above general author/topic overlap; preserve ambiguity rather than guessing.
- [ ] Evaluate security metadata on the complete original response, rank all available hits, deduplicate while preserving citation IDs, then cap the model-visible list. Keep referenced source URL mappings consistent with returned grounding.
- [ ] Search the distinctive filename first; add/rephrase owner/topic only when useful. Reuse returned candidates before spending another retrieval. Keep the default two-upstream-search limit configurable by the operator within explicit bounds.

## Task 4: Make failure stages explicit and retain cost controls

**Files:** Work IQ adapters/workflows, `config.py` if a query-limit setting is added, browser/API presentation only where necessary, related unit/integration tests and documentation.

- [ ] Keep separate structured outcomes for `no_matching_document`, `search_budget_exhausted`, `search_timeout`, unavailable resolution, access denied, expired/missing link, extraction failure and artifact failure.
- [ ] Ensure an empty completed query is not presented as timeout or proof of absent permissions. State that query limits reset with each user turn.
- [ ] Stop blindly repeating searches at the cap; use a supplied link or existing candidates instead. Continue avoiding automatic replay of potentially billed retrieval after timeout.
- [ ] Record durations and counts for resolution, search, download, extraction and rendering without file contents or private link tokens. Adjust time limits only if measurements establish a deadline bottleneck.

## Task 5: Verify the actual user journey

**Files:** `tests/integration/test_agent_tool_loop.py`, focused permission tests, opt-in live tests and `docs/DOCUMENT_READING.md`.

- [ ] Deterministic integration: supplied sharing link → resolve → opaque reference → real PPTX extraction → actual DOCX with source citation, with no indexed search call.
- [ ] Verify direct-link handling after the two-search cap, a correct ninth-ranked candidate, whole-file chunk coverage, cross-user/tenant/assertion denial, stale references, cancellation cleanup and no arbitrary-host requests.
- [ ] Run related unit/integration checks, full offline suite, Ruff, lockfile/diff checks and independent review.
- [ ] With the user's authenticated tenant context, verify the actual `3d-reconstruction-evolution` file can be resolved/read and a Word artifact can be opened. If blocked by policy/consent/billing, expose the actual stage rather than claiming success.
- [ ] Report measured request counts/latency separately from estimates. Do not promise zero fees or the globally cheapest strategy.

## Completion criteria

1. A valid, authorized sharing link reaches file content without indexed filename search.
2. Matching candidates available below the initial result cap are considered before reporting no match.
3. Search quota and elapsed-time errors are distinguished in the agent response.
4. Generated Word content is grounded in the selected document and retains a source citation.
5. Existing identity isolation, egress filtering, bounded caches and read-only source operations remain enforced.
6. Offline evidence and live tenant validation/blockers are reported separately.

## Primary references checked 2026-10-27

- [Microsoft Graph shared-item API, URL encoding and permissions](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0)
- [Work IQ retrieve: canonical URL scoping, delegated access and billing](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/retrieve)

## Direct-link implementation ledger

- Added validated organizational SharePoint/OneDrive URL encoding, exact read-only `/shares/u!…/driveItem` policy, `workiq_resolve_document_link`, owner/assertion-scoped opaque input/file references and direct-link-first agent instructions. Resolving links does not consume indexed-search quota.
- Authenticated live Work IQ reads confirmed that the supplied sharing link resolves to the requested file. The exact route and `structuredContent.results[].data` response envelope are verified, not inferred solely from Graph documentation. Capability discovery was replaced by this successful authenticated read.
- Ruling: include file size during resolution. The provided native PowerPoint is9.5MB and exceeds the Work IQ4MB binary cap. Native/converted MCP downloads could not return content, and a Work IQ-issued temporary download URL was rejected401. Do not add guessed download endpoints, silently redeem permissions, or increase timeouts to mask this failure.
- Ruling: linked files above4MB use separate delegated Graph OBO and the stable Graph v1.0 `/content` endpoint. No Work IQ token goes to Graph; no bearer/cookies go to redirected hosts. Only validated Microsoft content hosts are allowed, within bounded hops/bytes. No dependency on beta contentStream or on consuming Work IQ metadata download capabilities remains.
- Default extraction source cap remains5MB. Only the explicit cloud download path allows20MB; Office expansion remains50MB, native formats preserve their existing notes/table parsing, and text/page/slide/OCR limits remain unchanged.
- Live downstream consent check acquired an existing Graph token but confirmed no file permission. Therefore the actual user's large-file body is **not verified read**. Operator must configure delegated Microsoft Graph Files.Read and applicable consent on the backend app identified by AZURE_CLIENT_ID; no permissions, secrets or app registrations were changed automatically.
- TDD: new direct-link tests initially36 failing/4 passing; core resolver40 passing after implementation. Large native file/download tests initially failed before explicit cloud limits/Graph streaming. Integration covers search, small direct link and large direct link through real Agent Framework/PPTX parsing/DOCX rendering with mocked provider transport.
- Independent review identified input overflow URL leakage and HTTP transport DEBUG redirect-header leakage. Added regressions; valid excess/unregistered URLs now receive safe markers, and task-scoped filters cover HTTPX and concrete HTTP core loggers. Final review found no remaining Critical/Important issues. Separate Graph/Work IQ scope regression added.
- Current production functionality/tests are concrete, but live full-file validation is pending external Graph consent. Broader search ranking is deliberately outside this request.
- Final offline verification:589 passed,6 skipped,8 live tests deselected; one upstream Starlette/AnyIO deprecation warning. Ruff,19-file formatting check, lockfile and diff checks passed. Skips cover unavailable Playwright/bundled artifact-tool and a POSIX-only test. Full live body verification remains explicitly blocked by missing Graph file consent, not marked complete.
