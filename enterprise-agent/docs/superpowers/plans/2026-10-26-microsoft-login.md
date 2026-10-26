# Microsoft sign-in and local folders

**Goal:** Add the Microsoft sign-in flow described in the conversation, plus access to
Microsoft 365 and user-selected Windows folders for local development.

**Design:** Use the existing allowed Entra frontend registration and MSAL Browser
Authorization Code + PKCE with popup login and an explicit same-origin v5 redirect bridge
at `/auth/callback`. Vendor the pinned Microsoft distribution and license, keeping
scripts on this origin. Public auth bootstrap exposes only tenant/client/scopes;
the API keeps verifying access tokens and uses its existing OBO adapter. Tokens stay
in browser memory, are renewed silently when possible, and cleared on sign-out.

Windows folders are chosen explicitly through the browser picker. Upload supported files
to account-specific storage below `LOCAL_DOCUMENTS_ROOT`; authenticated tools cannot
read the existing shared local/demo root or another account's uploads. This avoids
granting every tenant user access to the host's files. Existing anonymous local demo
behavior remains; authenticated local imports are development-only. Microsoft 365
access follows the signed-in user's delegated permissions. File reads and artifact
creation are in scope; arbitrary edits/deletes and ACL grants are not.

**Implementation decisions:** Work in the current checkout to retain all existing
in-progress changes. Default the SPA client to the single allowlisted frontend ID;
permit an explicit `ENTRA_SPA_CLIENT_ID` when there are multiple. Registration of the
exact redirect URI in Entra is a user-side step after implementation.

## Tasks

- [x] Add failing auth bootstrap, callback/CSP and configuration tests.
- [x] Implement bootstrap, static MSAL assets/bridge and frontend login/logout/token renewal.
- [x] Add account-scoped folder import API and tools with isolation/path/limit tests.
- [x] Enable authenticated local tools only for development; add folder picker/progress UI.
- [x] Update ignored local configuration and Microsoft/local setup documentation.
- [x] Run offline suite, browser checks and independent review; document live-login limits.

**Review focus:** No secrets in public bootstrap; no unauthenticated protected API calls;
correct backend token scope; no stale user content on account changes/sign-out;
same-origin uncached auth bridge; limited Microsoft CSP origins; local path traversal,
links and cross-account access blocked; production local-file prohibition retained.

## Validation and handoff

- Full `uv run --with playwright pytest -q --tb=short`: **312 passed, 9 skipped**,
  one upstream Starlette/AnyIO deprecation warning. Skips are seven credential-gated
  live checks and two optional bundled artifact-tool renderer checks.
- Six real Chrome checks cover provider-boundary login, cancellation, token headers,
  folder imports, 60-file batching, stale history after sign-out, microphone permission
  and audio-closure races; the actual vendored MSAL library also initializes in Chrome.
- Existing `scripts/smoke_ui.py` passes navigation, streaming, downloads, history,
  responsive UI, approval fixtures and error states with zero external requests.
- Ruff, lockfile check and git whitespace check pass. Independent review findings were
  reproduced with failing browser tests and fixed; final review found no blockers.
- Actual local configuration reports browser login configured, Gemini and Work IQ ready;
  chat without login returns401. The configured SPA callback is
  `http://localhost:8000/auth/callback`. No persistent local server was left running.
- The user must add that exact SPA redirect URI to the frontend Entra registration.
  Live browser sign-in and Microsoft365 retrieval were not exercised by offline tests.
