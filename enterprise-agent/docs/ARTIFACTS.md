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

## Renderers

All six formats work with the packaged Python renderers. XLSX uses `openpyxl`; PPTX uses
`python-pptx`, with pagination for dense sections and source slides. Spreadsheet strings
are explicitly stored as text to prevent source content from becoming executable formulas.

An optional existing adapter supports `@oai/artifact-tool` 2.7.3+ through Node.js. To select
it instead of the portable Office renderers, set `ARTIFACT_TOOL_NODE_MODULES`, for example:

```text
ARTIFACT_TOOL_NODE_MODULES=C:\runtime\node_modules
ARTIFACT_NODE_EXECUTABLE=node
```

The optional package must be supplied by an approved runtime. `/health` reports
`artifact_tool_configured` for this optional adapter; a false value does not disable
XLSX/PPTX. Offline tests validate all portable formats, while optional Node tests may skip.

The spreadsheet creates a summary sheet, frozen table headers and a references sheet.
The portable presentation includes a cover, paginated sections/tables and references.
The optional Node presentation supports its existing layouts and speaker notes.

