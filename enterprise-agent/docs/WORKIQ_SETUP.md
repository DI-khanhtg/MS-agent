# Work IQ read-only setup

Phase 3-7 is implemented without storing tenant credentials in Git. Complete these steps in
Microsoft Entra and Microsoft 365 before enabling the integration.

## 1. Register the backend API

Create or select a confidential Entra application for the FastAPI backend:

1. Record its Directory (tenant) ID and Application (client) ID.
2. Under **Expose an API**, create a delegated scope named `access_as_user`.
3. Create a client secret for local/pilot use. Prefer a managed certificate or workload
   identity when the deployment phase adds support for it.
4. Add the delegated Work IQ permission `WorkIQAgent.Ask`.
5. Have an administrator grant tenant-wide consent for that Work IQ permission.

Work IQ must be enabled for the tenant and licensed for participating users. It does not
support application-only access.

## 2. Register/configure the client

The web or Teams client must request an access token for the backend API's
`access_as_user` scope. If the client has a separate app registration, record its client ID
so the backend can validate the token's `azp`/actor claim.

## 3. Configure `.env`

```dotenv
AUTH_ENABLED=true
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
