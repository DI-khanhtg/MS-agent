"""Transactional schema migrations for the existing SQLAlchemy Core store."""

import hashlib
import json

from sqlalchemy import inspect, text


def framework_owner(payload: str) -> str | None:
    session_id = json.loads(payload).get("session_id", "")
    parts = session_id.split(":", 2)
    if len(parts) == 3:
        return hashlib.sha256(":".join(parts[:2]).encode()).hexdigest()
    return None


def migrate(engine, metadata) -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(731924013)"))
        else:
            raise ValueError("Lifecycle storage supports SQLite and PostgreSQL only")
        try:
            metadata.create_all(conn)
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS agent_schema_migrations (version INTEGER PRIMARY KEY)"
            )
            versions = set(
                conn.execute(text("SELECT version FROM agent_schema_migrations")).scalars()
            )
            if 1 not in versions:
                columns = {c["name"] for c in inspect(conn).get_columns("agent_records")}
                if "expires_at" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE agent_records ADD COLUMN expires_at VARCHAR(40)"
                    )
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_agent_records_expiry "
                    "ON agent_records (expires_at, owner)"
                )
                # Framework session IDs were already qualified by verified tenant:user.
                # Unqualified legacy IDs cannot be assigned safely and remain untouched.
                rows = conn.execute(
                    text(
                        "SELECT key, payload FROM agent_records "
                        "WHERE namespace='framework_sessions'"
                    )
                ).all()
                for key, payload in rows:
                    owner = framework_owner(payload)
                    if owner:
                        conn.execute(
                            text(
                                "UPDATE agent_records SET owner=:owner "
                                "WHERE namespace='framework_sessions' AND key=:key"
                            ),
                            {"owner": owner, "key": key},
                        )
                conn.execute(text("INSERT INTO agent_schema_migrations (version) VALUES (1)"))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
