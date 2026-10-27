# Operations, backup and release

For the centrally hosted Microsoft sign-in service, start with [Shared hosting](SHARED_HOSTING.md).
The owner supplies server credentials and a public HTTPS origin; end users supply no env file.
Use persistent data storage and a single worker/replica for the current SDK session and upload
coordination. Aggregate request/chat admission limits are traffic controls, not a billing cap.

## Runtime boundaries

- Model/tool deadlines, retries and circuit breakers cap failing upstream requests.
- Tool calls are capped before side effects, and the SDK limits function iterations.
- SDK sliding-window compaction bounds model context; owner-scoped history retains the
  configured `MAX_HISTORY_MESSAGES`. Older history is removed, not silently synthesized.
- API rate limits use verified users (or remote address in local mode). Browser cross-origin
  writes and unknown hosts are blocked. Chunked upload bodies are bounded before parsing.
- Staging/production refuse demo/mock/local tools, disabled authentication, memory-only
  storage and disabled approval enforcement.
- External writes are never automatically retried. HTTP 202 means accepted, not delivered.

This version supports one API worker/replica per instance. SDK session objects and rate
limits are process-local. PostgreSQL records have optimistic concurrency checks and approval
execution claims are atomic, but horizontal scaling requires shared rate limits and session
leases. The deployment template therefore pins one replica.

### Turn deadlines and retry ownership

The Gemini runtime uses three separate budgets:

| Setting | Default | Scope |
| --- | --- | --- |
| `AGENT_TURN_TIMEOUT_SECONDS` | 300 seconds | Entire runtime turn, including session acquisition/lock waiting, model/tool work, retry backoff and session saving. Must be positive and at most 1800. |
| `AGENT_SYNTHESIS_RESERVE_SECONDS` | 15 seconds | Stop enterprise reads before the final deadline to leave time for a partial, grounded answer. Must be nonnegative and smaller than the turn timeout. |
| `GEMINI_TIMEOUT_SECONDS` | 60 seconds | Provider HTTP request timeout, rather than the duration of the entire tool loop. |

Work IQ, Fabric and knowledge reads share the remaining read window, including token
acquisition, network retries and document extraction. Existing per-source timeouts still
apply. Outside a runtime turn, adapters keep their configured behavior. The synthesis
reserve is best effort; if the model cannot finish by the total deadline, the API returns
the existing timeout error. Set the reserve to zero when using very short test deadlines.
Demo mode remains deterministic and makes no model/network calls.

A database commit already running in a worker thread can finish after the caller's
deadline. Its task remains tracked and updates the cached session version; the next
turn waits for it before using that session. That wait consumes the next turn's own
deadline. Cancellation also releases recovery-probe ownership in provider/MCP circuit
breakers, allowing later requests to recover without replaying writes. Cancellation
cannot forcibly stop a synchronous token-exchange or extraction worker already running.
Session deletion waits for any prior save to finish, then removes persisted and cached state
even if that earlier write failed. Turns queued behind deletion re-resolve a fresh entry.

The provider SDK's hidden HTTP retries are disabled. Runtime retries are bounded by
`GEMINI_MAX_RETRIES` and the single turn deadline, and stop after any tool attempt or
streamed text. Artifact attempts are recorded before rendering, so a provider failure
after creation cannot replay the whole turn and create a duplicate file. This is not
idempotency across separate client requests. Artifact calls still need a valid specification
and available tool budget; calls outside an agent turn through the artifact API remain valid.

Instructions are composed from enabled tool groups, with safety/approval/egress policy
always present. Capability filtering does not make another model call or route on keywords.

## Storage and secrets

`STORAGE_BACKEND=database` defaults to local SQLite at `DATABASE_PATH`. For PostgreSQL,
install the production extra and set `DATABASE_URL=postgresql+psycopg://...`. The versioned
`agent_records` table stores SDK sessions, owner-scoped conversations and approvals.
Access tokens and client secrets are not session state and are never serialized by storage.
Artifact files require a persistent volume; a database alone does not preserve downloads.

Database and backups contain enterprise content, including approved action payloads. Restrict
filesystem/database access and apply platform encryption. Inject production secrets through
the deployment secret store; do not bake `.env` into an image. The Bicep template uses secured
parameters and Container Apps secret references; Key Vault integration can replace those
references when your environment is connected. Separate staging and production databases,
artifact mounts, app registrations, and secret values.

## Audit, tracing and usage

`AUDIT_LOG_PATH` stores metadata-only JSONL request/chat events with request IDs, hashed
session/user identifiers, token usage and tool counts. Egress and approval decisions also
produce structured metadata events. No input, result body, bearer token or tool argument
content is written to the audit sink. `/api/metrics` exposes counters; authenticated instances
require the additional `metrics.read` scope. Provider token counts are available in chat
responses and SSE `done` events. Financial costs are not inferred from tool count.

`agent_turn` events include `stage` (`chat` or `stream`), `latency_ms`, `attempts`,
hashed owner/session identifiers and terminal `status` (`ok`, `error`, `timeout`, or
`cancelled`). Streaming adds `first_delta_ms` only when text was emitted. `source_read`
events record duration/status for fixed stages `workiq`, `workiq_document`, `fabric`,
and `knowledge`. Durations use a monotonic clock. These events omit queries, arguments,
result bodies and exception messages, and do not change API/SSE response fields.

Optionally set `INPUT_PRICE_PER_MILLION`, `OUTPUT_PRICE_PER_MILLION` and `COST_CURRENCY`
from your confirmed provider tariff. `/api/metrics` then estimates model cost from reported
tokens; otherwise it returns `null`. These process-lifetime counters cover completed turns,
exclude cache discounts, failed/billable calls, Speech/IQ/hosting costs, and are not an invoice.

`TELEMETRY_ENABLED=true` enables OpenTelemetry OTLP export after installing the production
extra. Configure `OTEL_EXPORTER_OTLP_ENDPOINT` and authentication for your collector or
Application Insights pipeline. It is disabled by default and no telemetry is exported during
offline tests. Configure retention/rotation for the local audit file at deployment.

## Backup and restore

Quiesce requests before backup to coordinate database rows with artifact writes. These
commands export a logical record snapshot and completed artifacts, excluding `.env`,
credentials, partial files and arbitrary document folders:

```powershell
uv run python scripts/backup.py --output data/backups/pilot-20261006.zip
```

Practice restore into a NEW empty database/artifact directory configured via process env:

```powershell
$env:DATABASE_PATH = 'data/restore-drill/agent.db'
$env:ARTIFACT_STORAGE_PATH = 'data/restore-drill/artifacts'
uv run python scripts/restore.py --input data/backups/pilot-20261006.zip
```

Restore refuses nonempty targets and unsafe archive paths. Confirm session history, artifact
integrity, owner isolation and approval status. Never automatically replay `executing`,
`failed` or `approved` actions after a crash; reconcile provider receipts first.

## Release, rollback and incidents

CI runs lint, deterministic evaluations and offline integration/security tests on Windows
and Linux. The release workflow is manual, uses Azure OIDC and GitHub environment protection,
and updates an existing Container App. Configure its Azure/ACR variables and environment
reviewers when connecting Azure. No cloud deployment is performed by local implementation.
Staging builds an image and records its digest. The production workflow requires that tested
`sha256:...` digest and promotes it without rebuilding; both environments must be able to
pull from the configured ACR. Deployment explicitly routes traffic to the new revision.

`infra/main.bicep` targets an existing Container Apps environment with an `agent-data` Azure
Files mount. Supply a tested image, authenticated hosts, PostgreSQL connection and tenant/app
settings. Deployment assets require cloud validation once credentials are available.
The Bicep template was compiled locally; the Docker image was not built on this Windows host
because Docker is unavailable. CI compiles the template again without deploying resources.

For release, back up state, deploy to staging, run opt-in live permission/egress/action tests,
review usage, then promote the same immutable image digest to production. Preserve the previous
revision. Roll back by routing traffic to that revision after checking schema compatibility;
restore into new storage when data rollback is needed, then switch the connection/mount.

For an incident: disable write groups, retain metadata logs and request IDs, check upstream
status and circuit breakers, isolate affected identity/tenant, revoke leaked credentials,
restore a known revision, and reconcile actions against provider records. A network timeout
must not be treated as proof that a write failed at the destination.
