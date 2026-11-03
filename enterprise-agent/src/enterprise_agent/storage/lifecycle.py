"""Owner-scoped, resumable local erasure and expiry cleanup."""

import asyncio
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from enterprise_agent.storage.database import Database
from enterprise_agent.storage.locking import async_file_lock, run_local
from enterprise_agent.storage.retention import RetentionPolicy  # public policy interface

__all__ = ["DataLifecycle", "RetentionPolicy", "session_owner"]

logger = logging.getLogger(__name__)


def session_owner(session_id: str) -> str:
    parts = session_id.split(":", 2)
    return ":".join(parts[:2]) if len(parts) == 3 else "runtime"


def remove_path(root: Path, relative: str) -> None:
    """Remove only validated children, never traversing a symlink or Windows junction."""
    from enterprise_agent.tools.local.documents import _is_document_link

    root = root.absolute()
    target = root / relative
    if not relative or target == root or root not in target.parents:
        raise ValueError("Cleanup target must be a child of its storage root")
    if any(part in {".", ".."} for part in Path(relative).parts):
        raise ValueError("Unsafe cleanup path")
    # Validate ancestors before touching anything, including configured root ancestors.
    for current in (*reversed(target.parents), target):
        if _is_document_link(current):
            raise OSError("Cleanup refuses filesystem links")
    if not target.exists():
        return
    if target.is_dir():
        for child in target.iterdir():
            remove_path(root, str(child.relative_to(root)))
        target.rmdir()
    else:
        target.unlink(missing_ok=True)


class DataLifecycle:
    def __init__(self, app) -> None:
        self.app = app
        self.settings = app.state.settings
        self.database = app.state.database
        if self.database is None:
            # Memory mode still needs durable file inventory and interrupted erasure intent.
            config = self.settings.model_copy(
                update={
                    "database_url": self.settings.database_url.__class__(""),
                    "database_path": self.settings.artifact_storage_path
                    / ".lifecycle"
                    / "state.db",
                }
            )
            self.database = Database(config)
        self.clock = lambda: datetime.now(UTC)
        self.lock_engine = None
        if self.database.engine.dialect.name == "postgresql":
            # Advisory locks must not exhaust the pool used by writes inside the lock.
            self.lock_engine = create_engine(self.database.engine.url, poolclass=NullPool)
        self._locks = tuple(asyncio.Lock() for _ in range(128))
        self._lock_root = self.settings.database_path.parent / ".lifecycle-locks"
        if self.database.engine.dialect.name == "sqlite":
            sqlite_path = self.database.engine.url.database
            if sqlite_path and sqlite_path != ":memory:":
                self._lock_root = Path(sqlite_path).absolute().parent / ".lifecycle-locks"
        if app.state.database is None:
            self._lock_root = self.settings.artifact_storage_path / ".lifecycle" / "locks"

    @asynccontextmanager
    async def locked(self, owner: str):
        """Owner is a SHA-256 digest, never an unverified user-provided selector."""
        if not re.fullmatch(r"[0-9a-f]{64}", owner):
            raise ValueError("Invalid lifecycle owner digest")
        async with self._locks[int(owner[:8], 16) % len(self._locks)]:
            if self.database.engine.dialect.name == "postgresql":
                connection = await run_local(
                    self.lock_engine.connect, _cancel_cleanup=lambda conn: conn.close()
                )
                lock_id = int.from_bytes(bytes.fromhex(owner)[:8], "big", signed=True)
                acquired = False
                try:
                    while not acquired:
                        acquired = await run_local(
                            lambda: connection.execute(
                                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}
                            ).scalar()
                        )
                        if not acquired:
                            await asyncio.sleep(0.025)
                    yield
                finally:
                    with anyio.CancelScope(shield=True):
                        try:
                            if acquired:
                                await run_local(
                                    lambda: connection.execute(
                                        text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id}
                                    )
                                )
                        finally:
                            await run_local(connection.close)
            else:
                # Fixed buckets avoid leaving identity-named lock files after erasure.
                bucket = int(owner[:8], 16) % len(self._locks)
                async with async_file_lock(self._lock_root / f"{bucket:02x}.lock"):
                    yield

    @asynccontextmanager
    async def request(self, principal):
        owner = Database.key(principal.isolation_key)
        async with self.locked(owner):
            try:
                await self.finish_pending(owner)
                await self.expire_owner(owner)
                yield
            finally:
                # Never leave user content or delegated token caches in another worker.
                with anyio.CancelScope(shield=True):
                    await self.evict(owner, durable_only=True)

    def _job(self, owner):
        with self.database.engine.connect() as conn:
            value = conn.execute(
                select(self.database.cleanup_jobs.c.payload).where(
                    self.database.cleanup_jobs.c.owner == owner
                )
            ).scalar()
        return json.loads(value) if value else None

    def _prepare(self, owner, full=False, roots=None):
        db = self.database
        if not full and not self.settings.retention_enabled:
            return
        with db.engine.begin() as conn:
            # Full erasure needs only the owner digest and directory roots. Avoid
            # loading conversation bodies just to delete their rows later.
            rows = (
                []
                if full
                else list(
                    conn.execute(
                        select(db.records).where(
                            db.records.c.owner == owner,
                            db.records.c.expires_at <= self.clock().isoformat(),
                        )
                    ).mappings()
                )
            )
            if not rows and not full:
                return
            paths = list(roots or [])
            if not full:
                for row in rows:
                    if row["namespace"] in {"artifacts", "uploads"}:
                        payload = json.loads(row["payload"])
                        paths.append([row["namespace"], payload["path"]])
            job = {"full": full, "rows": [[r["namespace"], r["key"]] for r in rows], "paths": paths}
            conn.execute(db.cleanup_jobs.insert().values(owner=owner, payload=json.dumps(job)))

    async def delete_user(self, principal):
        # Called only while the verified principal's request lock is held.
        owner = Database.key(principal.isolation_key)
        tenant = hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:24]
        user = hashlib.sha256(principal.object_id.encode()).hexdigest()[:24]
        await run_local(
            self._prepare,
            owner,
            True,
            [
                ["artifacts", f"{tenant}/{user}"],
                ["uploads", f"_users/{owner}"],
            ],
        )
        await self.finish_pending(owner)

    async def expire_owner(self, owner):
        await run_local(self._prepare, owner)
        await self.finish_pending(owner)
        await self.expire_memory(owner)

    async def finish_pending(self, owner):
        job = await run_local(self._job, owner)
        if job is None:
            return
        # Intent remains committed until all file I/O, cache and audit erasure succeed.
        roots = {
            "artifacts": self.settings.artifact_storage_path,
            "uploads": self.settings.local_documents_root,
        }
        for kind, relative in job["paths"]:
            await run_local(remove_path, roots[kind], relative)
        if job["full"]:
            await self.evict(owner)
            await run_local(self.app.state.audit.purge_subject, owner[:16])
        else:
            await self.evict(
                owner, sessions={key for ns, key in job["rows"] if ns == "framework_sessions"}
            )
        db = self.database

        def commit():
            with db.engine.begin() as conn:
                if job["full"]:
                    conn.execute(db.records.delete().where(db.records.c.owner == owner))
                else:
                    for namespace, key in job["rows"]:
                        conn.execute(
                            db.records.delete().where(
                                db.records.c.owner == owner,
                                db.records.c.namespace == namespace,
                                db.records.c.key == key,
                            )
                        )
                conn.execute(db.cleanup_jobs.delete().where(db.cleanup_jobs.c.owner == owner))

        await run_local(commit)

    async def evict(self, owner, *, durable_only=False, sessions=None):
        state = self.app.state
        store = getattr(state.agent_runtime, "_sessions", None)
        if store and (not durable_only or getattr(store, "database", None)):
            for key in list(store._sessions):
                if Database.key(session_owner(key)) == owner:
                    if sessions is not None and Database.key(key) not in sessions:
                        continue
                    entry = store._sessions[key]
                    await entry.wait_for_save(propagate_error=False)
                    store._sessions.pop(key, None)
                    if hasattr(store, "versions"):
                        store.versions.pop(key, None)
        if not durable_only and sessions is None:
            conversations = state.conversations
            for key in list(conversations._memory):
                if Database.key(key[0]) == owner:
                    conversations._memory.pop(key, None)
            approval_store = state.approval_service.store
            for key, item in list(approval_store._items.items()):
                if item.owner_key == owner:
                    approval_store._items.pop(key, None)
            # Buckets contain request activity only; aggregate metrics have no owner.
            for limiter in (state.limiter, state.shared_chat_limiter):
                for key in list(limiter._entries):
                    if Database.key(key) == owner:
                        limiter._entries.pop(key, None)
        tools = state.local_document_tools
        with tools._import_lock:
            for key in list(tools._document_references):
                if key[0].name == owner:
                    tools._document_references.pop(key, None)
        for provider in getattr(state, "token_providers", []):
            provider.evict_owner(owner)

    async def expire_memory(self, owner):
        if not self.settings.retention_enabled:
            return
        conversations = self.app.state.conversations
        for key, item in list(conversations._memory.items()):
            if (
                Database.key(key[0]) == owner
                and item.get("expires_at")
                and item["expires_at"] <= self.clock().isoformat()
            ):
                conversations._memory.pop(key, None)
        store = getattr(self.app.state.agent_runtime, "_sessions", None)
        if store:
            for key, entry in list(store._sessions.items()):
                if (
                    Database.key(session_owner(key)) == owner
                    and entry.expires_at
                    and entry.expires_at <= self.clock()
                ):
                    await store.delete(key)

    async def sweep(self):
        db = self.database

        def owners():
            with db.engine.connect() as conn:
                pending = set(conn.execute(select(db.cleanup_jobs.c.owner)).scalars())
                if self.settings.retention_enabled:
                    pending.update(
                        conn.execute(
                            select(db.records.c.owner).where(
                                db.records.c.expires_at <= self.clock().isoformat()
                            )
                        ).scalars()
                    )
                return pending

        pending = await run_local(owners)
        pending.update(Database.key(key[0]) for key in self.app.state.conversations._memory)
        store = getattr(self.app.state.agent_runtime, "_sessions", None)
        if store:
            pending.update(Database.key(session_owner(key)) for key in store._sessions)
        for owner in pending:
            try:
                async with self.locked(owner):
                    await self.finish_pending(owner)
                    await self.expire_owner(owner)
            except Exception:
                logger.warning("Lifecycle cleanup pending; retry scheduled", exc_info=False)
        await run_local(self.app.state.audit.maintain)

    async def run(self):
        while True:
            try:
                await self.sweep()
            except Exception:
                logger.warning("Lifecycle sweep failed; retry scheduled", exc_info=False)
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
