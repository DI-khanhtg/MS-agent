# Public local onboarding

## Agreed scope

Each person self-hosts locally and supplies their own Gemini and Entra configuration
once. Users have work/school accounts from different organizations. Each installation
uses its organization's approved tenant/app registrations; this is not an open,
shared multitenant service and must not weaken token/tenant/client validation.

## Design

- `uv run enterprise-agent setup` interactively creates an ignored `.env`, asking for
  Gemini key/model, tenant ID, backend app ID/secret and browser app ID. Secret inputs
  are hidden. Existing `.env` files are preserved, never overwritten by setup.
- `doctor` reports offline readiness and specific fixes without secrets or provider
  calls. It distinguishes configured credentials from verified consent/service access.
- `start --open` checks required settings before serving and opens the matching
  localhost URL. No-argument invocation remains an alias for start. Port conflicts
  produce actionable errors; a custom port must match the configured SPA redirect.
- `demo --open` works without `.env` or keys and never rewrites the live config. It
  disables authentication, IQ/LLM calls and optional speech for the demonstration.
- Add a placeholder-only `.env.example`, keep optional providers disabled, preserve
  durable account-isolated SQLite history, and correct Compose env forwarding.
- Rewrite root/project READMEs in Vietnamese with clone/setup/start, one-time Entra
  steps, ownership/data boundaries, troubleshooting, limitations and optional paths.
- Work IQ service enablement, billing, consent and permissions remain actions by
  each organization's administrator. Personal Microsoft accounts are not promised.

## Tasks

1. Add failing tests for setup serialization, no-overwrite/cancel behavior, tenant
   separation, safe diagnostics, demo isolation and launcher decisions.
2. Implement onboarding helpers and CLI in `main.py` while preserving the console entry.
3. Add configuration template and fix Compose/.dockerignore local behavior.
4. Rewrite README and update setup links; preserve upstream sample/license references.
5. Verify from an isolated checkout without `.env`, run full tests/lint/lock checks,
   request scoped review and publish the verified result on the existing clean branch.

## Validation

- Setup/diagnostics/launcher regression tests pass: exclusive file creation, credential
  serialization, cancelled/no-overwrite setup, independent tenant configs, safe JSON errors,
  callback port consistency and occupied port handling.
- Isolated source-copy test installs from cached locked dependencies with no developer
  `.env`, starts the real demo, creates an artifact, restarts on the same port and recovers
  chat history. A separate synthetic Microsoft configuration exposes a configured PKCE
  bootstrap; it does not claim real Entra/Work IQ service verification.
- Review found live host restrictions leaking into demo. Fixed by isolating demo's env-file
  source and allowing loopback hosts in launcher and ASGI settings; original environment and
  configuration remain unchanged. Demo also disables remote DB/Speech/telemetry and keeps
  separate demo state. Reviewer confirmed the final fix.
- `uv run --with playwright pytest -q --tb=short`: 433 passed, 10 skipped on Windows;
  skips cover live/optional checks and the POSIX-specific TCP restart regression.
- Ruff, formatting, lock consistency, doc links and whitespace checks pass.
- Compose structure and placeholder-only template are verified. Docker runtime/build was
  not tested because Docker is unavailable locally. Linux behavior remains for matrix CI.
- The existing authenticated localhost instance was not terminated and private `.env` was
  not overwritten. Publishing targets `feat/gemini-microsoft-documents`; main is not merged.
