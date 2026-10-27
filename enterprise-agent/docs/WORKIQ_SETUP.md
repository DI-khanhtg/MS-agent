# Work IQ read-only setup

Phase 3-7 is implemented without storing tenant credentials in Git. Complete these steps in
Microsoft Entra and Microsoft 365 before enabling the integration.

## 1. Register the backend API

Create or select a confidential Entra application for the FastAPI backend:

1. Record its Directory (tenant) ID and Application (client) ID.
2. Under **Expose an API**, create a delegated scope named `access_as_user`.
   Configure the backend to issue v2 access tokens (`api.requestedAccessTokenVersion=2`);
   the validator expects the tenant's v2 issuer and the backend client ID audience.
3. Create a client secret for local/pilot use. Prefer a managed certificate or workload
   identity when the deployment phase adds support for it.
4. Add the delegated Work IQ permission `WorkIQAgent.Ask`.
5. Have an administrator grant tenant-wide consent for that Work IQ permission.

Work IQ must be available for the tenant, with permissions, consent and the service's current
billing configuration. Confirm these in the tenant before live testing. This adapter uses
delegated access and does not implement application-only access.

### Indexed discovery of personal and shared documents

`workiq_search_documents` uses the read-only Work IQ `retrieve` tool with `grounding` and
the `OneDriveAndSharePoint` capability. It searches indexed documents accessible to the
signed-in user, including files in another owner's drive. It does not crawl every folder,
use the retiring `sharedWithMe` endpoint, or grant additional permissions.

This public-preview tool needs Work IQ enabled, delegated `WorkIQAgent.Ask` consent, and
usage-based Copilot Credits billing with a spending policy assigned to the calling user.
Before retrieval the client calls `tools/list`, discovers the deployed schema, and validates
the request. Missing/incompatible tools fail with a safe diagnostic, without guessing another
API or automatically falling back to an agent that could perform mutations.

The adapter allows at most two upstream retrievals per chat turn, does not automatically
retry potentially billed retrievals, and returns at most ten document references (five by
default). Successful identical searches and parsed file contents are reused only within the
current turn, scoped to the verified tenant/user/assertion. This bounds redundant work; it
does not guarantee a particular Microsoft/Gemini bill or latency. Search is not exhaustive,
and the indexed excerpts do not establish a complete file read.

If personal root listings work but discovery fails, check tool availability, consent, user
file access, spending-policy assignment and Work IQ MCP tenant policy. An empty result may
reflect indexing/coverage and must not be reported as proof of missing permissions. Follow
a returned validated owner-drive item reference to read actual content; do not guess IDs.
See [Microsoft's retrieve contract and prerequisites](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/retrieve).

## 2. Register/configure the client

The web or Teams client must request an access token for the backend API's
`access_as_user` scope. If the client has a separate app registration, record its client ID
so the backend can validate the token's `azp`/actor claim.

For the bundled browser UI, add the frontend client's **Single-page application** redirect
`http://localhost:8000/auth/callback`, and use **Kết nối Microsoft → Đăng nhập Microsoft**.
See [Microsoft login](MICROSOFT_LOGIN.md) for callback, token renewal and Windows folder
imports. The existing Desktop redirect used by the login-check script can remain.

## 3. Configure `.env`

```dotenv
AUTH_ENABLED=true
RUNTIME_MODE=gemini
STORAGE_BACKEND=database
AZURE_TENANT_ID=<home-tenant-guid>
AZURE_CLIENT_ID=<backend-api-client-id>
AZURE_CLIENT_SECRET=<backend-confidential-client-secret>
ENTRA_API_AUDIENCE=<backend-api-client-id>
ENTRA_REQUIRED_SCOPES=access_as_user
AUTH_ALLOWED_TENANT_IDS=<home-tenant-guid>
ENTRA_ALLOWED_CLIENT_IDS=<approved-frontend-client-id>

WORKIQ_MCP_URL=https://workiq.svc.cloud.microsoft/mcp
WORKIQ_SCOPE=api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask
ENABLED_TOOL_GROUPS=workiq_read,artifact_generation
```

For a staged migration you may use `mock_read,workiq_read,artifact_generation`, but production
should normally omit `mock_read` so synthetic data cannot be confused with company data.

For multiple explicitly approved tenants, provide comma-separated tenant IDs. Do not use
`common` as an allowlist value. The issuer and tenant of every user token are validated
before OBO.

## 4. Validate

Check the local client configuration, then sign in using the system browser:

```powershell
uv run python scripts/check_login.py --check-config
uv run python scripts/check_login.py
```

The client registration needs a **Mobile and desktop applications** redirect URI of
`http://localhost`. If multiple clients are allowlisted, select one with `--client-id`.
The helper requests only the backend scopes and validates the token with the application's
validator. It keeps tokens in memory and reports `LOGIN_OK`, or a failure with an AADSTS
code when available. It does not call Work IQ or an LLM. A preauthorized client can sign in
for its authorized scopes without user consent; see Microsoft's
[preauthorized applications reference](https://learn.microsoft.com/en-us/graph/api/resources/apiapplication).

To also check the backend client secret and downstream delegated consent, run:

```powershell
uv run python scripts/check_login.py --workiq-token
```

This performs an Entra on-behalf-of token exchange after validating the backend token.
`WORKIQ_TOKEN_OK` confirms token acquisition, not Work IQ service access or billing readiness.
This check does not query Microsoft 365 data or call the Work IQ service.

Check configuration without exposing secrets:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected fields include:

```json
{
  "auth_enabled": true,
  "entra_configured": true,
  "workiq_obo_configured": true,
  "enabled_tool_groups": ["artifact_generation", "workiq_read"]
}
```

Run the signed-in Work IQ integration test as documented in the main README. The test reads
at most one item from each of email, calendar, OneDrive, Teams chats, and people.

The Work IQ routing evaluation intentionally requires `ENABLED_TOOL_GROUPS=workiq_read` to
isolate tool-selection behavior. Restore `workiq_read,artifact_generation` afterward for the
Phase 7 Word/PDF/Excel/PowerPoint demos.

To execute the critical two-user permission test, select a private entity that User A can
read through an allowlisted `/me/...` path and User B cannot. Keep both access tokens only in
the process environment:

```powershell
$env:RUN_WORKIQ_PERMISSION_EVAL = "true"
$env:WORKIQ_USER_A_ACCESS_TOKEN = $userAToken
$env:WORKIQ_USER_B_ACCESS_TOKEN = $userBToken
$env:WORKIQ_USER_A_PRIVATE_ENTITY_URL = "/me/messages/<user-a-message-id>"
uv run pytest tests/integration/test_workiq_permissions_live.py -m live -v
```

The test first proves User A can read the entity, then requires the same request under User
B's delegated token to fail.
