# F3 implementation report

## Inspected implementation

- SQLAlchemy Core, one versioned `agent_records` table; SQLite by default and PostgreSQL via
  `DATABASE_URL`/the production extra. There was no migration framework before F3.
- Entra JWT validation produces `Principal`; identity is the verified `tenant_id:object_id`,
  with SHA-256 storage owner keys. Incoming bearer assertions are not stored in the database.
- Conversation history and approval state use that owner. Qualified framework sessions
  previously persisted under `runtime`; migration/restore now assign their actual owner.
- Artifact manifests/files use `data/artifacts/<tenant-hash>/<user-hash>/`; authenticated
  uploads use `data/documents/_users/<owner-hash>/`. Paths remain configurable.
- Microsoft source caches and handles are turn-local. Durable session/version caches, local
  document references, delegated token caching and interrupted renderer copies needed extra
  lifecycle handling. Rendering now uses registered workspaces under the owner artifact root.
- No B5 approval or retention duration was found. TTL enforcement defaults off and requires
  an administrator-supplied approval reference and explicit category durations.

## Changed files

All paths below are relative to `enterprise-agent/`. Neither `refund-agent-a365/` nor
`travel-agent-hosted/` was changed.

| Area | Files |
| --- | --- |
| Configuration/dependencies | `.gitignore`, `pyproject.toml`, `uv.lock`, `src/enterprise_agent/config.py` |
| Schema/retention/coordination | `src/enterprise_agent/storage/database.py`, `src/enterprise_agent/storage/migrations.py` (new), `src/enterprise_agent/storage/retention.py` (new), `src/enterprise_agent/storage/locking.py` (new), `src/enterprise_agent/storage/lifecycle.py` (new) |
| API/lifespan/authentication context | `src/enterprise_agent/api/data.py` (new), `src/enterprise_agent/api/app.py`, `src/enterprise_agent/api/auth.py`, `src/enterprise_agent/api/local_documents.py` |
| History/framework sessions | `src/enterprise_agent/storage/conversations.py`, `src/enterprise_agent/agent/session.py`, `src/enterprise_agent/agent/runtime.py` |
| Artifacts/temporary copies | `src/enterprise_agent/artifacts/models.py`, `src/enterprise_agent/artifacts/store.py`, `src/enterprise_agent/artifacts/service.py`, `src/enterprise_agent/artifacts/renderers.py` |
| Uploads/token cache lifecycle | `src/enterprise_agent/tools/local/documents.py`, `src/enterprise_agent/tools/workiq/auth.py`, `src/enterprise_agent/tools/fabric/auth.py` |
| Audit/recovery | `src/enterprise_agent/telemetry/operations.py`, `scripts/restore.py` |
| Tests | `tests/unit/test_lifecycle.py` (new), `tests/integration/test_data_lifecycle.py` (new), `tests/security/test_data_deletion.py` (new), `tests/integration/test_agent_tool_loop.py` |
| Documentation | `README.md`, `ARCHITECTURE.md`, `docs/data-lifecycle.md` (new), `docs/superpowers/plans/2026-10-30-f3-data-lifecycle.md` (new), this report |

The FastAPI minimum is 0.121 for explicit request-scoped dependency teardown; the locked
version remains unchanged. Agent Framework, model/provider configuration, Graph/Work IQ OBO
exchange, consent scopes, egress policies and approval transitions are preserved. Delegated
token retention is disabled within shared MSAL applications; this can increase OBO requests.

## Verification

Run from `enterprise-agent/` (the verified environment uses `.venv/Scripts/python.exe`):

```powershell
uv run --extra dev pytest tests/unit/test_lifecycle.py tests/integration/test_data_lifecycle.py tests/security/test_data_deletion.py -q
uv run --extra dev pytest tests/unit tests/integration tests/security evals -q -m "not live"
uv run --extra dev ruff check src tests scripts
uv lock --check --offline
git diff --check
```

Focused tests: **22 passed, 1 skipped**. Final offline suite: **721 passed, 4 skipped,
8 deselected**, in 133.65 seconds. Ruff, lock consistency and diff checks passed. Tests use temporary
local roots, injected token validators/offline runtimes and fake clocks; no live Microsoft
or LLM services are called. Existing live tests remain opt-in.

The coverage includes fixed expiry deadlines, disabled/unapproved policy, legacy DB and
snapshot migration, all four retention categories, memory mode, full upload/chat/artifact
erasure, usage/action metadata, tenant and user isolation, idempotency, failed filesystem
cleanup and restart recovery, independent OS-process locks, an active SSE response's final
save, cancelled thread writes/action creation, renderer scratch copies, and audit rotation,
retention and interrupted rewrite copies. Independent review findings were reproduced with
failing tests before fixes.

## Remaining risks and unverified assumptions

- B5 approval and production durations remain external operator decisions. Pre-F3 records
  with no deadline need an approved backfill; enabling TTL does not invent a retroactive one.
- Real PostgreSQL advisory-lock behavior, shared mounts, encrypted storage and infrastructure
  snapshot/PITR retention were not exercised here. PostgreSQL requires session-affine locks
  and DB connection capacity; SQLite requires reliable host-volume OS locking. All replicas
  must share data roots/configuration. Memory mode enforces one process per storage root.
- Online erasure is logical deletion, not a secure overwrite of DB free pages, WAL, replicas,
  snapshots, downloaded files, Microsoft source data or external provider records. Restores
  must replay the organization's separately controlled erasure ledger before serving traffic.
- Old unqualified framework session IDs and pre-F3 OS-temp renderer leftovers cannot safely
  be attributed automatically. Inventory and resolve them offline before rollout.
- Optional Node artifact-tool renderer checks and POSIX-specific restart checks are unavailable
  on this host; Windows symlink creation is also unavailable. Python Office rendering and
  actual Windows multiprocess locking are covered. A Starlette/AnyIO deprecation warning is
  emitted by the installed test client.

Operational details and recovery procedures are in [data-lifecycle.md](data-lifecycle.md).
