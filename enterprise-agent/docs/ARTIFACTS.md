# Artifact generation - Phase 7

The agent and API use one validated `ArtifactSpec` for Markdown, JSON, DOCX, XLSX, PPTX,
and PDF. A request must include a title and at least one section or table. Nested limits on
text, rows, columns, sources, and metadata bound memory use before rendering.

## API

Create a Project X Word status report:

```powershell
$body = @{
  artifact_type = "docx"
  title = "Project X Status Report"
  subtitle = "Weekly update"
  template_id = "data_impact"
  sections = @(
    @{ heading = "Executive summary"; body = "Project X remains on track." },
    @{ heading = "Risks"; bullets = @("Confirm the test window.", "Close the owner gap.") }
  )
  tables = @(
    @{
      title = "Open actions"
      columns = @("Action", "Owner", "Due date")
      rows = @(@("Confirm testing", "An", "2026-09-20"))
    }
  )
  sources = @(
    @{ label = "Project X weekly note"; reference = "workiq://files/project-x-weekly" }
  )
} | ConvertTo-Json -Depth 8

$artifact = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/artifacts `
  -ContentType "application/json" -Body $body
Invoke-WebRequest "http://127.0.0.1:8000$($artifact.download_url)" `
  -OutFile $artifact.filename
```

With authentication enabled, use the same bearer token for creation, listing, and download.
`GET /api/artifacts` lists only the verified tenant/user's records. A download ID is resolved
inside the same owner namespace and checked against its SHA-256 manifest; callers cannot
submit a filesystem path.

The chat tool group `artifact_generation` exposes `create_artifact`. The agent first retrieves
and verifies read-only enterprise evidence, then sends one complete structured specification
to the renderer. Creating a local output file never grants permission to update Microsoft 365.

## Company templates

`config/artifacts.yaml` is the allowlist. A template controls company name, primary/accent
colors, font, and optionally a DOCX base template under `templates/artifacts/`. API and model
callers select only `template_id`; they cannot provide a template path. Add template files to
the configured root, then reference them by relative path in the server-owned YAML.

## XLSX and PPTX runtime

Markdown, JSON, DOCX, and PDF use packaged Python renderers. XLSX and PPTX use
`@oai/artifact-tool` 2.7.3+ through Node.js. Set `ARTIFACT_TOOL_NODE_MODULES` to the directory
that contains `@oai/artifact-tool`, for example:

```text
ARTIFACT_TOOL_NODE_MODULES=C:\runtime\node_modules
ARTIFACT_NODE_EXECUTABLE=node
```

The package is not available from the public npm registry. It must be supplied by the
approved runtime used to build/deploy this service. `/health` reports
`artifact_tool_configured`; when it is false, only XLSX/PPTX requests return 503 and the other
formats remain available.

The spreadsheet renderer creates a readable summary sheet, freezes headers, applies semantic
formatting, and creates one worksheet per structured table. The presentation renderer follows
the neutral Codex Grid cover, two-column content, and evidence-table layouts and writes source
blocks into speaker notes.

