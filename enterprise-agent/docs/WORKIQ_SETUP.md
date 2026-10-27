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

## Large linked files and Microsoft Graph

Sharing links are resolved through the existing delegated Work IQ `fetch` shared-item read
route. This was verified against an authenticated tenant. Small files use `fetch_blob`;
linked files above 4 MB use Microsoft Graph v1.0 `/drives/{drive}/items/{item}/content`,
with a separate Graph-audience on-behalf-of token and a 20 MB application cap.

The operator configures the following once; ordinary users keep the same Microsoft login:

1. Open **Entra → App registrations** and select the backend app whose Application ID
   matches `AZURE_CLIENT_ID`, **not** the SPA app identified by `ENTRA_SPA_CLIENT_ID`.
2. Open **API permissions → Add a permission → Microsoft Graph → Delegated permissions**.
3. Add **Files.Read**. This is a delegated read permission; don't choose application-only
   permissions or add file-write permissions for this download path.
4. Complete consent for the organization. Use **Grant admin consent** with an authorized
   administrator where required. OBO cannot open a downstream consent prompt itself;
   multi-organization deployments need applicable consent in each user's tenant.
5. Restart the backend, sign in again and resend the file link. No new Gemini key, Entra
   app registration or browser Graph access token is needed.

`MsalGraphFileTokenProvider` requests `https://graph.microsoft.com/.default`, using only
permissions already configured/consented for the existing backend application. It does not
grant permissions or repurpose the Work IQ token. `document_graph_consent_required` means
the delegated Graph token cannot be acquired/configured; `document_graph_access_denied`
means Graph rejected the request, so check consent, the user's file access and tenant policy.
Do not fix either condition by increasing timeouts or repeatedly searching the index.

Graph content may redirect to a short-lived Microsoft download URL. Only explicitly
validated HTTPS SharePoint and Microsoft file hosts are followed, with no bearer/cookies
on redirect hops, no ambient proxy credentials, bounded redirects/bytes and task-scoped
redaction of HTTP transport logs. Raw download capabilities never enter model results.
Parser expansion/page/text limits remain enforced, and local uploads keep their limits.

Live verification on 2026-10-27 resolved the supplied shared PowerPoint successfully. Its
9.5 MB source exceeded Work IQ's binary cap; an unauthenticated temporary download was
rejected, and existing Graph consent lacked file scopes. Full live content validation
therefore remains pending the operator's delegated file permission/consent. Offline tests
cover both native small-file and Graph large-file reads through actual Office extraction
and Word rendering. No Entra permission changes were performed by code.

References: [Graph download contract and least permissions](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0),
[shared-item resolution](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0).

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
