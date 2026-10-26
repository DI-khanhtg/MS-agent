"""Small versioned record store; transactions also coordinate approval execution."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, select

from enterprise_agent.config import PLACEHOLDER_VALUES, Settings


class StorageConflict(RuntimeError):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        url = settings.database_url.get_secret_value()
        if url in PLACEHOLDER_VALUES:
            settings.database_path.parent.mkdir(parents=True, exist_ok=True)
            url = "sqlite:///" + settings.database_path.as_posix()
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url, pool_pre_ping=True)
        metadata = MetaData()
        self.records = Table(
            "agent_records",
            metadata,
            Column("namespace", String(40), primary_key=True),
            Column("key", String(64), primary_key=True),
            Column("owner", String(64), nullable=False, index=True),
            Column("version", Integer, nullable=False),
            Column("payload", Text, nullable=False),
            Column("updated_at", String(40), nullable=False),
        )
        metadata.create_all(self.engine)

    @staticmethod
    def key(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, namespace: str, key: str, owner: str) -> tuple[dict, int] | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(self.records).where(
                        self.records.c.namespace == namespace,
                        self.records.c.key == self.key(key),
                        self.records.c.owner == self.key(owner),
                    )
                )
                .mappings()
                .first()
            )
        return (json.loads(row["payload"]), row["version"]) if row else None

    def put(self, namespace: str, key: str, owner: str, payload: dict, version: int) -> int:
        values = dict(
            payload=json.dumps(payload, ensure_ascii=False),
            version=version + 1,
            updated_at=datetime.now(UTC).isoformat(),
        )
        with self.engine.begin() as conn:
            if version == 0:
                from sqlalchemy.exc import IntegrityError

                try:
                    conn.execute(
                        self.records.insert().values(
                            namespace=namespace, key=self.key(key), owner=self.key(owner), **values
                        )
                    )
                except IntegrityError as exc:
                    raise StorageConflict("Record was created concurrently") from exc
            else:
                result = conn.execute(
                    self.records.update()
                    .where(
                        self.records.c.namespace == namespace,
                        self.records.c.key == self.key(key),
                        self.records.c.owner == self.key(owner),
                        self.records.c.version == version,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise StorageConflict("Record changed concurrently; reload before retrying")
        return version + 1

    def list(self, namespace: str, owner: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(self.records.c.payload)
                .where(
                    self.records.c.namespace == namespace,
                    self.records.c.owner == self.key(owner),
                )
                .order_by(self.records.c.updated_at.desc())
            ).all()
        return [json.loads(row[0]) for row in rows]

    def delete(self, namespace: str, key: str, owner: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                self.records.delete().where(
                    self.records.c.namespace == namespace,
                    self.records.c.key == self.key(key),
                    self.records.c.owner == self.key(owner),
                )
            )
        return result.rowcount == 1
