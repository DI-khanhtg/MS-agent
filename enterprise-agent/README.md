# Enterprise Agent

Start with [Local testing](docs/LOCAL_TESTING.md) for the zero-network demo, folder reads,
voice setup, and all six artifact formats. See [Implementation status](docs/IMPLEMENTATION_STATUS.md)
and [Operations](docs/OPERATIONS.md) for persistence, limits, backup, tracing, and deployment.
Open `http://127.0.0.1:8000` after starting the backend to use the local web interface.

For **Sign in with Microsoft** and account-specific Windows folder imports, follow
[Microsoft login for local development](docs/MICROSOFT_LOGIN.md). Use `http://localhost:8000`
for that flow, matching the registered SPA callback. The UI manages tokens and OBO access.

Use the chat paperclip to attach PDF, Word(DOCX), PowerPoint(PPTX), Excel(XLSX) or text
documents. The same bounded reader handles local sources and Work IQ downloads. See
[Document reading](docs/DOCUMENT_READING.md) for supported content, examples and OCR limits.

Self-hosted Phase 0-12 implementation from the repository master plan. It provides a
FastAPI API, Gemini Flash through Microsoft Agent Framework, read-only Work IQ and
Fabric Data Agent MCP integrations, Microsoft Entra delegated authentication/OBO,
multi-source reasoning, optional Foundry IQ retrieval, six artifact formats, voice input,
durable state, a local web interface, and approval-gated Microsoft 365 actions.
Live integrations and Azure deployment remain unverified until credentials are connected.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- Portable Python renderers generate XLSX/PPTX; the existing Node renderer is optional
- A Gemini API key for live chat and live evaluations
- A Microsoft Entra app and Work IQ tenant access for real Microsoft 365 queries
- A published Fabric Data Agent on supported capacity for governed business queries

## Setup

The repository intentionally contains no `.env.example`. Edit the ignored `.env` file.
For Gemini chat, create an API key in [Google AI Studio](https://aistudio.google.com/apikey)
and configure:

```dotenv
RUNTIME_MODE=gemini
GEMINI_API_KEY=PUT_YOUR_GEMINI_API_KEY_HERE
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
GEMINI_MODEL=gemini-3.8-flash
GEMINI_TIMEOUT_SECONDS=60
GEMINI_MAX_RETRIES=2
```

Replace the placeholder key with your real key. `GEMINI_MODEL` can select another model
available to your key. The client uses Google's
[OpenAI-compatible Chat Completions API](https://ai.google.dev/gemini-api/docs/openai),
including streaming and function calling. The OpenAI SDK is the transport library;
requests go to Google. Gemini tool-call thought signatures are preserved in session history.
Use `RUNTIME_MODE=demo` to test without API keys, then run:

```powershell
uv sync --extra dev
uv run uvicorn enterprise_agent.api.app:app --reload --host 127.0.0.1 --port 8000
```

The local setup supports folder reads and artifact generation. Unauthenticated demo use is
available with `AUTH_ENABLED=false`. `RUNTIME_MODE=gemini` makes external API requests and requires
a Gemini key. Previous `DEEPSEEK_*` settings are no longer read; deployments must use
`GEMINI_*` and `RUNTIME_MODE=gemini`. `/health` reports `gemini_configured`.
See [Work IQ setup](docs/WORKIQ_SETUP.md) and [Fabric setup](docs/FABRIC_SETUP.md) before
enabling real enterprise data. See [Controlled actions](docs/ACTIONS.md) before enabling
`workiq_write`.

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Non-streaming chat:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/chat `
  -ContentType 'application/json' `
  -Body '{"message":"What is the status of Project Alpha?"}'
```

When `AUTH_ENABLED=true`, add the API access token:

```powershell
$headers = @{ Authorization = "Bearer $accessToken" }
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/chat `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"message":"Find my latest email about Project Alpha."}'
```

Streaming uses Server-Sent Events (SSE):

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/api/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"message":"Summarize Project Alpha and its requirements."}'
```

## Tests and evaluations

Run deterministic unit/API tests and validate the evaluation dataset:

```powershell
uv run pytest -m "not live"
uv run ruff check .
```

After configuring a real key, run the Gemini tool-selection evaluation:

```powershell
$env:RUN_LIVE_EVALS = "true"
uv run pytest evals/test_tool_selection_live.py -m live -v
```

The live suite evaluates every case in `evals/datasets/tool_selection.json` and requires
the configured pass-rate threshold. It incurs Gemini API usage.

After Entra and Work IQ are configured, validate email, calendar, files, Teams, and people
using an API access token supplied only through the process environment:

```powershell
$env:RUN_WORKIQ_LIVE_EVALS = "true"
$env:WORKIQ_USER_ACCESS_TOKEN = $accessToken
uv run pytest tests/integration/test_workiq_live.py -m live -v
uv run pytest evals/test_workiq_tool_selection_live.py -m live -v
```

Never save `WORKIQ_USER_ACCESS_TOKEN` in `.env` or commit it.

After publishing and configuring a Fabric Data Agent, validate the MCP adapter and routing:

```powershell
$env:RUN_FABRIC_LIVE_EVALS = "true"
$env:FABRIC_USER_ACCESS_TOKEN = $accessToken
uv run pytest tests/integration/test_fabric_live.py -m live -v
uv run pytest evals/test_fabric_tool_selection_live.py -m live -v
```

For the combined Work IQ + Fabric + artifact routing evaluation, enable all three groups and
set `RUN_MULTI_SOURCE_LIVE_EVALS=true`. Access tokens are process-only secrets.

## API

- `GET /` — local chat, folders, voice, downloads, history, and authenticated approval UI.
- `GET /api/config` — sanitized capabilities for the current runtime.
- `GET /api/metrics` — token/tool counters and optionally configured model cost estimate.
- `GET /api/sessions`, `GET /api/sessions/{id}`, `DELETE /api/sessions/{id}` — owner history.
- `POST /api/voice/transcribe` — bounded WAV input to the Azure Speech adapter.
- `GET /health` — liveness and configuration status; it never exposes secrets.
- `GET /api/me` — sanitized identity derived from the validated bearer token.
- `POST /api/chat` — complete response with session ID and recorded tool calls.
- `POST /api/chat/stream` — SSE events: `metadata`, `delta`, `done`, or `error`.
- `POST /api/artifacts` — validate and create Markdown, JSON, DOCX, XLSX, PPTX, or PDF.
- `GET /api/artifacts` — list artifacts owned by the verified tenant/user.
- `GET /api/artifacts/{artifact_id}/download` — ownership-checked artifact download.
- `POST /api/actions/drafts` — validate and create a local action draft.
- `POST /api/actions/{action_id}/request-approval` — move a draft to pending approval.
- `GET /api/actions` and `GET /api/actions/{action_id}` — owner-scoped action status.
- `POST /api/approvals/{action_id}/approve` — authenticated approval and backend execution.
- `POST /api/approvals/{action_id}/reject` — authenticated rejection without execution.

Send a prior `session_id` to continue a conversation. The server namespaces it by verified
tenant and user, so two users cannot share conversation state by guessing the same ID.
Set `STORAGE_BACKEND=database` for durable SDK sessions, conversation history and approvals.
SQLite is the local default; PostgreSQL is supported with the production extra.

## Security defaults

- Application-only Work IQ access is not implemented; every Work IQ call uses OBO for the
  verified signed-in user.
- Work IQ reads expose `fetch`, bounded `fetch_blob` document extraction, read-only
  `get_schema`, and `search_paths`.
- Fabric exposes one read-only `fabric_query` adapter and uses the signed-in user's access.
- Resource paths are allowlisted and bounded; mutation tools and unsafe paths are rejected
  before any network request.
- User input, Work IQ output, and Fabric output pass through `config/egress.yaml` before
  reaching Gemini.
- The model can draft and request approval, but only an authenticated backend endpoint can
  approve and execute. Delete is not implemented or exposed.
- Credentials/secrets are blocked, configured PII is redacted, collections and strings are
  truncated, and oversized payloads are rejected.
- Egress audit logs contain metadata and hashed subject identifiers, not raw payloads.

See [Security](docs/SECURITY.md) for the trust boundaries and production checklist.
See [Artifact generation](docs/ARTIFACTS.md) for the schema, Word demo, templates, and renderers.
