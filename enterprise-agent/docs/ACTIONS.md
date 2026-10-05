# Controlled Microsoft 365 actions

Phase 10 implements a server-enforced workflow:

```text
draft → pending approval → approved → executing → succeeded/failed
                         ↘ rejected/expired
```

The model-visible tools can create a draft, submit it for approval, and read status. They
cannot approve or execute. Approval is accepted only through an authenticated API endpoint,
which atomically claims the action and invokes the Work IQ mutation. Repeated approval cannot
execute an action twice in this process.

Supported actions are send email, create calendar event, post a Teams chat message, and
update a narrowly allowlisted entity. `delete_entity` is blocked in the model registry,
Work IQ policy, and `config/approvals.yaml`.

Microsoft currently documents `create_entity`, `update_entity`, and `do_action` as Work IQ
MCP mutation tools. Mutation requests are evaluated by tenant policy and are blocked by
default until an administrator enables supported operations. The delegated
`WorkIQAgent.Ask` permission requires admin consent and includes read/write access scoped to
the signed-in user's accessible resources.

Official references:

- [Work IQ MCP tool reference](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/tool-reference)
- [Work IQ permissions](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/permissions)

## Enable

Complete `docs/WORKIQ_SETUP.md`, have an administrator configure Work IQ mutation policy,
review `config/approvals.yaml`, then use:

```dotenv
AUTH_ENABLED=true
REQUIRE_WRITE_APPROVAL=true
APPROVAL_POLICY_PATH=config/approvals.yaml
ENABLED_TOOL_GROUPS=workiq_read,fabric_read,artifact_generation,workiq_write
```

The runtime refuses to enable `workiq_write` when Entra/OBO is incomplete or backend
approval enforcement is disabled. Controlled-action API routes require verified Entra
identity even if the rest of the app is in anonymous development mode.

## API example

Create and submit a draft:

```powershell
$headers = @{ Authorization = "Bearer $accessToken" }
$draft = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/actions/drafts `
  -Headers $headers -ContentType 'application/json' `
  -Body '{"action_type":"send_email","payload":{"to":["owner@example.com"],"subject":"Project update","body":"Please review."}}'

Invoke-RestMethod -Method Post `
  "http://127.0.0.1:8000/api/actions/$($draft.action_id)/request-approval" `
  -Headers $headers
```

After the UI displays the exact summary and payload, approve through the backend endpoint:

```powershell
Invoke-RestMethod -Method Post `
  "http://127.0.0.1:8000/api/approvals/$($draft.action_id)/approve" `
  -Headers $headers
```

A `provider_accepted` verification means Work IQ returned HTTP 202: accepted for processing,
not proof of email delivery or downstream completion.

## Current operational boundary

Approval state is in-memory and single-process for this phase. It is lost on restart and is
not safe for multiple API workers. Keep one worker for pilot use. Durable transactional
storage, idempotency keys across restarts, distributed locking, retention, and richer audit
export belong to Phase 12 and are required before production deployment.
