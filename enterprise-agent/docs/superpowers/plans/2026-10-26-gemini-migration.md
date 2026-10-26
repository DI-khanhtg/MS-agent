# Gemini API migration

**Goal:** Use Google's Gemini API for the enterprise-agent LLM, as requested.

**Architecture:** Retain FastAPI and Microsoft Agent Framework. Use the existing
OpenAI SDK against Google's OpenAI-compatible Chat Completions endpoint, replacing
the Responses client. Default to `gemini-3.8-flash`, configurable with `GEMINI_MODEL`.
Preserve Gemini tool-call metadata across streaming, tool results and saved sessions.

**Constraints:** Preserve existing workspace changes, demo mode, enterprise tools,
approval enforcement, egress checks, runtime deadlines and retry ownership. Do not
reuse a DeepSeek secret as a Gemini key. Keep historical review/plan documents intact.

- [x] Update configuration/factory/API/runtime tests for Gemini; verify failures.
- [x] Migrate runtime settings and callers to Gemini Chat Completions, with metadata hooks.
- [x] Exercise tools, streaming, session persistence and metadata via mocked HTTP.
- [x] Update UI, deployment, current documentation and ignored local environment.
- [x] Run offline pytest suite, Ruff and configuration readiness; report live-test limits.

**Review focus:** Correct `/chat/completions` endpoint; no stale DeepSeek keys or
mode checks; thought signatures survive tool loops; offline/demo remains usable;
tool calls are not replayed by provider retries.

**Validation:** Full `uv run --no-sync pytest -q --tb=short`: 254 passed, 9 skipped
(7 credential-gated live checks, 2 optional artifact-tool checks), one upstream
Starlette/AnyIO deprecation warning. Ruff and `uv lock --check` pass. Independent
review found no blocking issues. Configuration readiness reports Gemini mode with
the placeholder key unconfigured; no live Gemini call was made. Docker/Bicep CLI
validation was unavailable locally.

**Implementation decisions:** Keep edits in the existing workspace to preserve the
user's in-progress changes. Use the existing SDK and public framework hooks without
adding dependencies. Keep the old provider key untouched in the ignored local `.env`,
where it is ignored, and add a separate Gemini placeholder. Docker Compose forwards
Gemini settings from the local environment. Historical design/review documents remain
unchanged.
