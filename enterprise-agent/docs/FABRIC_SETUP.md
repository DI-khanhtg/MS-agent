# Fabric Data Agent setup

Phase 8 uses a published Microsoft Fabric Data Agent as a read-only MCP server. The adapter
uses streamable HTTP, performs `initialize` and `tools/list`, discovers the single tool and
question argument at runtime, and calls it with a delegated Fabric bearer token.

Microsoft's current prerequisites are paid Fabric F2+ or Power BI Premium P1+ capacity,
Fabric tenant settings required for AI processing, at least one supported data source, read
access to that source, and a published Data Agent. Review the external-processing warning
before allowing results to pass through the project's DeepSeek egress gate.

Official references:

- [Fabric Data Agent as MCP server](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
- [Sharing and underlying data permissions](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-sharing)

## Create and publish

1. In the target Fabric workspace, create a Data Agent and attach only the warehouse,
   lakehouse, semantic model, KQL database, mirrored database, or ontology it needs.
2. Select the relevant tables, add precise instructions and representative example queries.
3. Test business questions such as budget versus actual, utilization, revenue, cost, and KPI.
4. Give the Data Agent a detailed description. Orchestrators use this description for routing.
5. Publish it. The MCP endpoint does not work against an unpublished agent.
6. In **Settings → Model Context Protocol**, copy the MCP server URL. Also record the
   workspace ID and Data Agent ID.
7. Share the published agent and underlying source read access with pilot users. Fabric
   continues to apply RLS and CLS; do not grant Build/Write merely for querying.

## Configure delegated access

The backend exchanges the verified incoming API token for a user-scoped Fabric token via
OAuth OBO. Configure the backend Entra application and admin consent appropriate to your
tenant, then set:

```dotenv
AUTH_ENABLED=true
AZURE_TENANT_ID=<tenant-guid>
AZURE_CLIENT_ID=<backend-client-id>
AZURE_CLIENT_SECRET=<backend-secret>
ENTRA_API_AUDIENCE=<backend-client-id>
AUTH_ALLOWED_TENANT_IDS=<tenant-guid>

FABRIC_WORKSPACE_ID=<workspace-guid>
FABRIC_DATA_AGENT_ID=<data-agent-guid>
FABRIC_MCP_URL=TO_BE_FILLED_LATER
FABRIC_SCOPE=https://api.fabric.microsoft.com/.default
ENABLED_TOOL_GROUPS=workiq_read,fabric_read,artifact_generation
```

When `FABRIC_MCP_URL` remains a placeholder, the application derives the official endpoint
from both IDs. If supplied explicitly, it must be HTTPS on `api.fabric.microsoft.com` under
the `/v1/mcp/workspaces/` path. `/health` must report `fabric_obo_configured: true` before a
live test.

## Validate

Keep the incoming user token only in the process environment:

```powershell
$env:RUN_FABRIC_LIVE_EVALS = "true"
$env:FABRIC_USER_ACCESS_TOKEN = $accessToken
uv run pytest tests/integration/test_fabric_live.py -m live -v
uv run pytest evals/test_fabric_tool_selection_live.py -m live -v
```

The routing dataset asserts that email/calendar/Teams/files/people use Work IQ, while
budget, actuals, utilization, revenue, cost, and KPI queries use Fabric.
