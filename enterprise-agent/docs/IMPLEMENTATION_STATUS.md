# Master plan implementation status

## Shared links and large native documents (2026-10-27)

The agent resolves organizational SharePoint/OneDrive links directly through Work IQ,
without indexed search, and uses private current-turn link/document references. Authenticated
live verification confirmed the supplied link resolves. For linked files above the MCP4MB
binary cap, the backend uses separate delegated Graph OBO with existing consent and bounded
native downloads up to20MB; browser/local upload caps and parser expansion limits remain.
Graph bearer tokens are pinned to Graph; validated redirects carry no bearer/cookies, and
HTTP transport logs are redacted for that task. Small files retain Work IQ blob reads.

Deterministic integration verifies small/large shared PPTX → real text extraction → DOCX.
The actual supplied9.5MB file's body remains pending operator Graph Files.Read consent;
existing live Graph consent had no file scope. No claim of live content success is made.
See [Work IQ large-file setup](WORKIQ_SETUP.md#large-linked-files-and-microsoft-graph).

## Indexed shared-document discovery (2026-10-27)

Work IQ document search now uses read-only `retrieve` (`grounding`, OneDrive/SharePoint),
validates the deployed MCP schema, caps upstream searches at two per turn and avoids
automatic replay of potentially billed retrievals. Owner/assertion-scoped opaque references
resolve other drives and numeric IDs without relying on redacted paths. Extensionless
titles are resolved through a narrow filename metadata read. Search is explicitly
non-exhaustive; errors and empty results cannot establish missing file permissions.

Parsed files and successful retrievals are reused in an eight-entry RAM cache during the
turn, explicitly cleared/closed on exit and cancellation. Document reads support bounded
14000-character windows with full-text PII masking before splitting and sanitized-text
offsets. Deterministic Agent Framework integration covers shared PPTX discovery/read and
actual DOCX generation; the artifact tool also validates dictionary arguments before use.
Live `retrieve` availability, billing, the user's shared file and actual latency/cost savings
still require tenant validation. See [Work IQ setup](WORKIQ_SETUP.md) and
[document reading](DOCUMENT_READING.md).

## Document reading (2026-10-26)

The shared local/Work IQ reader handles selectable-text PDF, DOCX, PPTX, XLSX and UTF-8
text. The chat paperclip accepts document attachments; WAV uploads are in the connection
dialog's speech section. Extraction metadata reports text-only coverage and missing text
instead of treating slide/sheet labels as content. See [Document reading](DOCUMENT_READING.md).
Offline/browser verification: 379 passed, 9 skipped. OCR and legacy binary Office conversion
are not included; live tenant document retrieval still requires validation.

## Microsoft browser login and local imports (2026-10-26)

The bundled UI supports Microsoft sign-in/sign-out, in-memory MSAL token renewal and
owner-scoped Windows folder imports in development alongside Work IQ. The SPA callback
must be registered in Entra before live browser use. See
[Microsoft login](MICROSOFT_LOGIN.md). Full offline/browser suite:312 passed,9 skipped;
live browser authentication and Microsoft365 retrieval remain to be verified with the tenant.

## Gemini migration (2026-10-26)

The current LLM provider is Gemini via Google's OpenAI-compatible Chat Completions API.
Use `RUNTIME_MODE=gemini` and `GEMINI_*` settings; `/health` reports `gemini_configured`.
The client preserves tool-call thought signatures through streaming and durable sessions.
The DeepSeek entries below describe earlier implementation milestones. Live Gemini access
requires a configured key and has not been verified by offline tests.

All listed phases have a local implementation or deployment/operations artifact. This does
not mark live acceptance criteria as passed. Microsoft permissions, billing, real retrieval,
two-user isolation against a tenant and cloud deployment require later connection/testing.

| Phase | Implementation | Offline validation |
|---|---|---|
| 0 Bootstrap | Typed settings, lockfile, package, Docker | Imports/config/lint |
| 1 DeepSeek core | Agent Framework Responses client, chat/SSE, bounded calls | Mock HTTP tool loop |
| 2 Mock tools | Deterministic samples plus explicit zero-network demo | Dataset and API |
| 3 Work IQ reads | MCP/OBO, safe paths, document extraction | Adapter/schema/egress |
| 4 Auth/isolation | Entra v2 JWT validation, tenant/client/scope checks | Fake JWT/provider tests |
| 5 Egress | Classification, deny, redact, bounded results | Security suite |
| 6 Read-only MVP | Project/meeting/requirements workflows and local UI | Routing fixtures |
| 7 Artifacts | DOCX/PDF/XLSX/PPTX/Markdown/JSON, references, downloads | All portable formats |
| 8 Fabric | Published Data Agent MCP adapter, delegated OBO | MCP mocks/routing |
| 9 Multi-source | Communications + metrics workflows, optional Foundry IQ | Scripted tool loop |
| 10 Actions | Draft/review/approve/reject, durable CAS claim, no write retry | Policy/state/API tests |
| 11 Voice | WAV validation, Azure Speech adapter, mic/upload UI, same chat | Injected transcriber |
| 12 Hardening | SQLite/PostgreSQL state, limits/circuits, audit/OTLP, backup/restore, CI/release | Offline operations tests; Bicep compiles |

Deliberately future scope from the plan: multi-agent orchestration, custom vector DB/crawlers,
model/GPU hosting, browser automation, realtime voice/barge-in and a generic planner engine.
Single-replica deployment is explicit until distributed session/rate-limit support is added.

The UI accepts a backend access token when Entra is connected. Browser/Teams interactive
sign-in configuration, live two-user permissions and external provider acceptance tests are
connection-stage work; the backend JWT/OBO implementation and mocked isolation tests exist.

See `LOCAL_TESTING.md` to run now and `OPERATIONS.md` for staged rollout. The original Microsoft
refund/travel samples are retained without edits.

Local validation on 2026-10-06: Ruff passed; 177 offline tests passed, 2 optional Node-renderer
tests skipped and 7 live tests deselected. Chrome UI smoke passed for streaming, DOCX download,
history and disconnected voice with external browser requests blocked. A local backup/restore
drill passed, and Bicep 0.48.1 compiled `infra/main.bicep`. Docker/cloud deployment, real Speech,
DeepSeek tool-selection acceptance, and tenant IQ/permission tests remain unrun.

Pre-live review on 2026-10-19: 197 offline tests passed, 2 optional Node-renderer tests
skipped, and 7 live tests deselected. Ruff and the Chrome UI smoke check passed; the UI
check used an isolated demo database, disabled speech, and blocked external requests.
Regression coverage now includes nested MCP transport errors, text JSON write receipts,
and stopping enterprise routing evaluations when demo mode or disabled auth would invalidate
the result. Writes remain single-attempt. The local configuration still reports demo mode
with Fabric, Work IQ and DeepSeek unconfigured; no live provider access was verified.

Flow reliability update on 2026-10-20: artifact attempts now participate in replay/tool-budget
protection. The DeepSeek HTTP timeout is separate from the total turn deadline; enterprise
reads share a synthesis reserve, and the provider SDK's HTTP retries are disabled. Prompts
include only enabled capability guidance. Metadata-only turn/source audits expose duration,
attempts and first-text timing without changing the API/SSE contract.

Independent review identified and verified fixes for cancelled database saves, recovery-probe
ownership and session deletion racing successful/failed background writes. Queued turns after
deletion use fresh framework state. Final verification: 253 offline tests passed, 2 optional
Node-renderer tests skipped, 7 live tests deselected; Ruff and diff whitespace checks passed.
The suite retains one upstream Starlette/AnyIO deprecation warning. No provider/tenant live
validation or measured latency/cost improvement is claimed. See `FLOW_REVIEW.md` and
`OPERATIONS.md` for implemented scope, defaults and cancellation limitations.
