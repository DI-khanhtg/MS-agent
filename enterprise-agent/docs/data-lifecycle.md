# Data lifecycle operations (F3)

## Policy and configuration

No approved B5 retention policy was found in this repository. Automatic deletion is disabled
by default. The following variables do **not** prescribe production retention durations:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RETENTION_ENABLED` | `false` | Enable expiry assignment, access filtering and cleanup |
| `RETENTION_POLICY_APPROVAL` | empty | Administrator's approved B5 policy/change reference |
| `CONVERSATION_TTL_SECONDS` | unset | Conversation history retention from first persistence |
| `SESSION_TTL_SECONDS` | unset | Agent Framework session retention from first persistence |
| `ARTIFACT_TTL_SECONDS` | unset | Generated file retention from creation |
| `UPLOAD_TTL_SECONDS` | unset | Uploaded local copy retention from import |
| `CLEANUP_INTERVAL_SECONDS` | `60` | Operational scan frequency, not a retention period |
| `AUDIT_MAX_BYTES` | `10000000` | Rotate the active audit segment by size |
| `AUDIT_RETENTION_SECONDS` | unset | Approved time limit for audit events, including active segment |

Enabling retention without an approval reference fails configuration validation. Audit time
retention also requires that reference. An unset category TTL means no automatic expiry for
that category. All timestamps are UTC. Deadlines are fixed at creation, not extended on reads
or updates. History and model session state have independent deadlines; both must match the
approved policy because framework state can also contain conversation content.

Set the approved values through the existing environment/secret deployment mechanism and
restart every worker with the same configuration. Changing a TTL affects newly created
records only. Turning enforcement off preserves existing deadlines without applying them;
turning it back on resumes cleanup. Audit rotation always runs; no segment count or time
limit is invented while B5 is pending. Monitor disk space until audit retention is approved.

## Schema and upgrade

`storage/migrations.py` applies versioned, transactional migrations on database startup.
SQLite uses `BEGIN IMMEDIATE`; PostgreSQL uses a transaction advisory lock. Version 1 adds
nullable indexed `agent_records.expires_at`, records the schema version in
`agent_schema_migrations`, and creates `agent_cleanup_jobs`. Qualified legacy framework
sessions are reassigned from the shared `runtime` owner to the SHA-256 tenant:user owner.
Neither access tokens nor new plaintext identity fields are persisted.

Existing rows retain `expires_at = NULL`; old artifact manifests accept a missing deadline.
Pre-F3 uploads and artifacts are still removed by self-service deletion, which removes the
entire verified owner's directory, including orphan/temporary files. They are not assigned
an arbitrary retroactive expiry. An approved backfill must first inventory old data, verify
ownership and define how the B5 policy treats pre-existing data. Unqualified legacy framework
IDs cannot be safely attributed: quarantine and investigate them offline before production
rollout. Do not guess an owner or delete them as another user's data.

File registrations in the `artifacts` and `uploads` namespaces contain relative paths only,
not file bodies. Registration precedes file creation so a crash cannot create an untracked
new file. Renderer scratch copies live under the same owner's artifact subtree, with a
registered workspace created before rendering; normal completion removes the workspace,
and explicit deletion or an approved artifact TTL also covers interrupted renders. Legacy
OS-temp renderer copies from pre-F3 process crashes cannot be reliably assigned to an owner;
inventory and remove those offline before rollout using the host's approved temp-data policy.
Interrupted audit rewrite duplicates are removed under the audit writer lock. Memory mode
stores this inventory/journal in
`ARTIFACT_STORAGE_PATH/.lifecycle/state.db`; conversation and model state remain in RAM.

## Self-service deletion

Send `DELETE /api/me/data` with the normal Entra bearer access token. The endpoint uses only
the verified principal's `tenant_id` and `object_id`. It accepts no body or query parameters.
Alternate user/tenant headers never select the owner. Anonymous requests are rejected.

Success is `204`, including repeated requests. The operation removes local conversation and
framework session rows, action drafts/approval metadata, usage records, artifact manifests
and files, all owner upload copies, local document references, session/version caches and
owner activity buckets. Audit usage events with that pseudonymous subject are removed from
active and rotated files. Anonymous aggregate process metrics cannot be attributed back to
a user and remain. OS lock files use a bounded shared bucket pool, not identity filenames.

Work IQ and Graph source-read caches/handles already close at the end of a turn. Durable
session caches and local document references now also clear before releasing each request's
owner lock. Shared MSAL applications retain tenant configuration but use a non-retaining
delegated token cache; OBO exchange, audiences, scopes and consent remain unchanged. This
avoids user tokens surviving in an idle worker after another worker handles deletion.

Deletion never invokes Microsoft 365 delete/write APIs. It does not remove Microsoft source
documents, previous external writes, client downloads/browser copies, LLM provider records,
or data held by external observability systems. Those systems have their own retention and
deletion processes. The client may start fresh activity after deletion completes.

## Cleanup, concurrency and recovery

FastAPI lifespan starts a retrying cleanup loop. Every authenticated API request also drains
its owner's pending cleanup and due expiries before admitting new activity. FastAPI 0.121+
is required for explicit request-scoped dependency teardown
([FastAPI dependency lifecycle](https://fastapi.tiangolo.com/advanced/advanced-dependencies/)).
A per-owner lock
is held until the full response finishes, including SSE streams and file downloads. Requests
for an identity serialize, including ongoing approved actions; deleting data waits for their
completion. Local file/DB work running in a thread is joined before a cancelled request can
release its lock. No approval-state transitions are changed.

Cleanup commits an intent containing only owner digests, record keys and relative paths,
then removes files/cache/audit data, then atomically deletes selected records and the intent.
A failure may leave already-removed files with retained records; the pending intent prevents
that owner from resuming ordinary API activity until retries finish. Missing files are safe
to retry. Symlinks and Windows junctions are refused. Storage roots must be service-owned
and inaccessible to untrusted filesystem writers. Do not change roots while jobs are pending.

Filesystem failure returns `503`; fix the underlying mount/permission/link problem and retry
DELETE. The lifespan loop also retries, including after restart. A DB failure can return a
server error; do not interpret it as successful deletion. Inspect pending work with:

```sql
SELECT owner, payload FROM agent_cleanup_jobs;
SELECT namespace, COUNT(*) FROM agent_records
WHERE expires_at IS NOT NULL GROUP BY namespace;
SELECT version FROM agent_schema_migrations;
```

SQLite workers require the **same local host volume** and reliable OS file locks; separate
copies of SQLite or network filesystems without reliable locking are unsupported. PostgreSQL
instances use session advisory locks on dedicated unpooled connections (reserve DB connection
capacity accordingly). Use direct/session-affine connections, not transaction-pooled PgBouncer.
All instances must share the same artifact, upload and audit mounts, settings and schema.
Independent local disks cannot provide global file erasure. Memory mode is development-only
and enforces a single active application process on its storage root. Use database mode for
multiple workers/instances. Route all local data operations through the API lifecycle guard;
standalone administrative writers must be quiesced during cleanup.

## Encryption, backups and deletion limitations

Use the deployment's existing at-rest encryption: managed PostgreSQL encryption and encrypted
managed storage, or an encrypted SQLite host volume covering the DB, journal/WAL files,
artifacts, uploads, audit segments and temporary files. Enforce this in infrastructure and
verify it with the hosting provider/volume tooling. The application does not implement field
encryption, manage encryption keys or certify the underlying volume's encryption status.

B5 must also approve backup/snapshot retention, replica/PITR retention and provider log
retention; this feature assigns no durations to those systems. Operators must configure
those controls separately and record the maximum recovery window. Online DELETE is logical
erasure, not guaranteed secure overwrite of SQLite free pages, WAL, PostgreSQL MVCC pages,
SSD blocks, filesystem snapshots or replicas. These remnants age out under infrastructure
retention and encryption/key management.

The existing `scripts/backup.py` exports DB records and completed artifacts; it excludes
credentials, uploads, audit files and cleanup journals. `scripts/restore.py` accepts old
record snapshots without `expires_at`. These scripts are **not** a full lifecycle-consistent
online backup protocol. Stop writers, finish pending cleanup, then take a consistent managed
DB/volume snapshot of every relevant data root. If using the helper archive, separately
account for excluded data and do not claim uploads can be restored from that archive.

Restoring a snapshot can restore deleted users' data. Maintain a separately controlled erasure
ledger under the approved policy, restore into an isolated environment, reapply deletions and
expiry cleanup, validate owner isolation, and only then reopen traffic. Do not automatically
replay approved Microsoft actions. Expire historical backups using the approved infrastructure
policy; the API does not rewrite immutable/offline backup media. Secure the erasure ledger
and retain only the pseudonymous identifiers needed for replay, for its approved duration.

## Offline verification

From `enterprise-agent/`:

```powershell
uv run --extra dev pytest tests/unit/test_lifecycle.py tests/integration/test_data_lifecycle.py tests/security/test_data_deletion.py -q
uv run --extra dev pytest tests/unit tests/integration tests/security -m "not live" -q
uv run --extra dev ruff check src tests scripts
```

The new tests inject offline runtimes and token validators and use temporary SQLite/filesystem
roots and fake clocks. They call no live Microsoft or model services. PostgreSQL advisory-lock
behavior, encryption and shared-mount semantics require deployment validation before rollout.
