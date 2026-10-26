# Chat Markdown rendering

**Goal:** Fix literal Markdown shown in assistant messages and improve chat readability.

**Approach:** Render assistant responses/history with pinned local Marked18.1.1 plus
DOMPurify3.4.17. Keep user messages as literal text. Escape raw HTML, sanitize parsed
output with an explicit tag/attribute allowlist, permit only safe web/mail links and
same-origin relative links; do not load model-supplied images or active HTML. Format
paragraphs, headings, lists, tables, quotes and code using the existing visual palette.
Batch streaming DOM updates per animation frame and flush at completion/error. Retain
the raw response in a WeakMap so intermediate rendered HTML is never reused as Markdown.

- [x] Add failing browser checks for Markdown, streaming/history, malicious HTML and mobile layout.
- [x] Vendor versioned parser/sanitizer with licenses and verified npm integrity manifests.
- [x] Implement safe shared rendering for new bubbles, streaming updates and saved history.
- [x] Style readable chat content, scrollable tables/code and accessible links.
- [x] Run browser and full suite, inspect rendered screenshots, obtain focused review.

**Constraints:** Preserve existing workspace changes, sign-in and account isolation.
No external CDN dependency or model/API calls are needed for validation. Work in the
existing checkout, retaining prior development. A new whole-page layout is outside this fix.

**Validation:** Full `uv run --with playwright pytest -q --tb=short`:318 passed,9 skipped,
one existing Starlette/AnyIO deprecation warning. The six new Chrome tests cover Markdown
structure, literal user input, unsafe HTML/URLs/images, streamed completion/error and
history, authenticated artifact downloads, and390px layout. Ruff passes. Independent
review found no Critical/Important issues. Desktop1440px/mobile390px screenshots were
inspected and show no horizontal page overflow or browser script errors.

**Additional behavior:** Markdown artifact links use the existing authenticated API
download path, with account-generation checks. User-visible history keeps the original
Markdown. An initial screenshot attempt found the user's port8000 stopped; preview and
validation used temporary isolated local servers instead, with synthetic content.
