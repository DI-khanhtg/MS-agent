# Local testing without external connections

For the offline demo, use the command below. No `.env` or keys are needed; it overrides
only the running process configuration and preserves the real `.env`. The demo
makes no Gemini or Microsoft IQ requests and disables auth, Speech, telemetry, and remote
database connections for that process. Demo state uses `data/demo.db`, generated files use
`data/artifacts/demo`, and its audit log uses `data/demo-audit.jsonl`.
For live Gemini and Microsoft sign-in, follow [Self-host setup](SELF_HOST_SETUP.md): each
person configures their own local backend, keys and organization tenant once.

```powershell
cd MS-agent/enterprise-agent
uv run enterprise-agent demo --open
```

Open <http://127.0.0.1:8000>. In the document panel, choose folder `Project-X`, select an
output format, and press **Tạo bản nháp**. Downloads are available in **Tài liệu của bạn**
and the recent-document list. The sidebar also provides conversation history and approvals;
**Kết nối Microsoft** opens the sign-in dialog. On mobile, use the menu button.
Prompt suggestions fill the composer without sending. Press Enter to send, or Shift+Enter
for a new line.
The included documents are explicitly synthetic. Chat supports streaming, conversation
history, and artifact downloads in DOCX, PDF, XLSX, PPTX, Markdown, and JSON.

Demo output is a deterministic template with source excerpts and unconfirmed assumptions.
It is not LLM reasoning or a real IQ retrieval result. Arbitrary enterprise reasoning uses
`RUNTIME_MODE=gemini` after supplying a key; this change can be made independently of IQ.

## Your own documents

Copy supported files under `data/documents/<folder-name>/`, or set `LOCAL_DOCUMENTS_ROOT`
to a dedicated document directory. Tools accept relative paths, block hidden files,
traversal and links, and enforce file/text/tool-call limits. Supported inputs: UTF-8
TXT/Markdown/CSV/JSON, DOCX, PPTX, PDF, XLSX. PPTX includes grouped text, tables and speaker
notes. Scanned PDFs need OCR, which is not implemented. See [Document reading](DOCUMENT_READING.md).
Unsupported, empty, or truncated inputs must be reported as incomplete coverage.

Anonymous local reads use the configured demo root. In authenticated development, use
**Chọn thư mục trên máy** to import documents into account-specific storage. Existing shared
demo files are not automatically exposed to signed-in users. See
[Microsoft login](MICROSOFT_LOGIN.md). Local reads remain disabled in staging/production;
enterprise document access uses delegated Work IQ.

## Voice

The UI records PCM WAV (16 kHz, mono, 16-bit) up to 59 seconds, or accepts a WAV upload.
`POST /api/voice/transcribe` returns text, language, duration and provider. Send the transcript
to the same chat endpoint. No second voice agent or duplicate business workflow is used.

Install `uv sync --extra dev --extra voice`, then configure `SPEECH_PROVIDER=azure`,
`SPEECH_KEY`, `SPEECH_REGION`. Default language is `vi-VN`. Without those settings, the API
returns a clear 503 and the UI explains that speech is disconnected. Tests inject a fake
transcriber; they do not pretend to recognize an audio recording without an STT provider.
Azure SDK `recognize_once` is intended for a single command/utterance. Continuous multi-turn
speech, barge-in and long dictation remain future features in the master plan.

## Connect later

1. Set `RUNTIME_MODE=gemini`, supply `GEMINI_API_KEY`, keep local/mock groups to test LLM.
2. Follow `WORKIQ_SETUP.md` for backend/client registrations, v2 access tokens, consent,
   and tenant access; use `workiq_read,artifact_generation` with `AUTH_ENABLED=true`.
3. Follow `FABRIC_SETUP.md` to publish a Data Agent before enabling `fabric_read`.
4. `foundry_read` is optional. Set `FOUNDRY_IQ_MCP_URL` to the knowledge base MCP endpoint.
   Default `FOUNDRY_IQ_AUTH_MODE=entra` uses OBO for `https://search.azure.com/.default`.
   The backend needs delegated consent and the querying user needs search access.
   Development API-key mode also accepts `FOUNDRY_IQ_API_KEY`; it is blocked outside dev.
   This adapter only permits an explicitly shared knowledge base after setting
   `FOUNDRY_IQ_SHARED_KNOWLEDGE=true`. It does not claim automatic per-document ACL handling
   for private sources. Configure source ACL synchronization/forwarded identity separately
   before adding private knowledge. Tool input schemas are discovered at runtime.
5. Enable `workiq_write` only after delegated reads work. Approval/execution still require
   an authenticated user. No approval can be submitted by an LLM tool. Demo has no external
   write executor. The local suite covers approval with fake users/providers.

`uv run enterprise-agent doctor` reports readiness without printing secrets or
calling any service. `/health` is a configuration/liveness check, not proof of live access.

## Validation

```powershell
uv run ruff check .
uv run pytest -m "not live" -q
```

The suite tests the real Agent Framework tool loop with HTTP mock responses, source routing,
egress, auth/isolation, approval enforcement, durable state, folder-to-artifact flows,
voice validation, and operational limits. Tests for an optional private Node renderer may
skip; all six portable formats are tested. Live tests remain opt-in and can incur charges.

Optional UI check with an installed Chrome browser:

```powershell
uv run --with playwright python scripts/smoke_ui.py
```

This drives the local demo, downloads a DOCX, restores history, checks the disconnected
voice state, and fails if the page attempts an external request. It does not install a browser.

Live setup stores sessions/approvals in `data/agent.db`; artifacts use owner-isolated directories.
The `demo` command uses its separate storage paths described above.
Those outputs and your documents are ignored by Git. The bundled Project-X sample remains
versioned. Do not publish a dev instance with authentication disabled to other users.

## Flow reliability checks

Run the artifact replay, deadline, capability prompt and streaming regressions offline:

```powershell
uv run pytest tests/integration/test_artifact_runtime.py tests/unit/test_turn_budget.py tests/unit/test_runtime.py tests/unit/test_workflows.py tests/integration/test_api.py -q
```

These use real artifact generation with temporary storage and injected provider failures.
They check that a provider error after artifact creation cannot create another file,
retry/queue waiting cannot extend the turn deadline, source reads leave a synthesis
reserve, concurrent users remain isolated, and a failed stream never emits `done`.
No credential or live provider is needed.

`GEMINI_TIMEOUT_SECONDS` now applies to a provider HTTP request. Configure the separate
`AGENT_TURN_TIMEOUT_SECONDS` (default 300) for an entire turn and
`AGENT_SYNTHESIS_RESERVE_SECONDS` (default 15) for the read cutoff. See `OPERATIONS.md`
for validation, timing audit events and replay limitations. Demo mode remains unchanged.
