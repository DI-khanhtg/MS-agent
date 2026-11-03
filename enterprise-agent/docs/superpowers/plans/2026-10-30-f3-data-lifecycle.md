# F3 Data Lifecycle Implementation Plan

**Goal:** Remove only a verified user's local data and enforce explicitly approved TTLs.
**Architecture:** Extend the existing SQLAlchemy Core record store with versioned migrations,
nullable UTC `expires_at`, owner-scoped cleanup journals and file registrations. Serialize
requests and cleanup for each identity using PostgreSQL advisory locks or SQLite host-volume
file locks. Keep the lock through streaming and file responses. Retry journaled operations
before admitting more work. Evict durable caches after each request so other workers cannot
retain deleted content. Preserve all provider, OBO, egress and approval transitions.
**Policy:** No approved B5 policy was found. Enforcement defaults to disabled; operators must
supply an approval reference and explicit TTLs. No production duration is chosen here.

## Files and steps

- [x] Add offline expiry/migration tests in `tests/unit/test_lifecycle.py`, full local API
  deletion/isolation/retry tests in `tests/integration/test_data_lifecycle.py`, and hostile
  identity input tests in `tests/security/test_data_deletion.py`. Run them to confirm RED.
- [x] Add `storage/migrations.py`, `storage/locking.py`, `storage/lifecycle.py`; extend
  `storage/database.py` and `config.py`. Use durable cleanup intent before filesystem I/O;
  delete records only after file cleanup succeeds. Preserve intent on cancellation/error.
- [x] Integrate `api/data.py`, `api/auth.py`, `api/app.py`, `agent/session.py`,
  `storage/conversations.py`, `artifacts/models.py`, `artifacts/store.py`,
  `tools/local/documents.py`, OBO cache lifecycle, and `telemetry/operations.py`.
- [x] Document encryption prerequisites, backup/snapshot policy and restore limitations in
  `docs/data-lifecycle.md`, README and architecture. Maintain compatibility with old backups.
- [x] Run lifecycle tests, existing offline suite and Ruff; review concurrency, symlinks,
  legacy ownership, failure recovery and unapproved TTL behavior before reporting results.

## Review focus

Expiry must not reset on read; cleanup cannot race an active stream or download. A failed
filesystem operation must retain cleanup intent, and a second DELETE must converge. Never
infer identity from URL/query/body/header overrides. Never follow links out of storage roots.
Legacy artifacts/uploads with no expiry remain until explicit deletion or an approved,
operator-controlled backfill. SQLite deployment requires a shared encrypted host volume;
PostgreSQL replicas require the same mounted data roots. Backups are outside online erasure.

## Progress

Initial inspection complete: SQLite/PostgreSQL, SQLAlchemy Core `create_all`, no existing
migration runner. Framework sessions used owner `runtime`; conversations/actions use the
SHA-256 tenant:user owner. Artifact manifests and uploads were filesystem-only. Work IQ
source handles/read cache are turn-local and cleared in `turn_budget`; local document
references and MSAL application token caches survive requests and require eviction.

Independent review found two important issues: cancelled action creation retained its cache,
and old backups bypassed framework owner reassignment. Both were reproduced with failing
tests and fixed; the regression tests passed. A fake-clock regression also reproduced and
fixed a final conversation save crossing its expiry deadline without extending that deadline.
Renderer scratch copies were moved into registered owner workspaces, and stale audit rewrite
copies are purged. These two changes also have RED-to-GREEN regressions. Focused validation:
22 passed, 1 skipped (Windows symlink creation unavailable). Final offline suite:
721 passed, 4 skipped, 8 deselected in 133.65 seconds. Ruff, offline lock consistency and
diff checks passed. No live Microsoft or LLM services were called.
