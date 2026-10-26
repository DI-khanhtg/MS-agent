"""Restore into an EMPTY database/artifact root. No in-place destructive recovery."""

import argparse
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from sqlalchemy import select

from enterprise_agent.config import Settings
from enterprise_agent.storage.database import Database


def restore(settings: Settings, source: Path) -> None:
    database = Database(settings)
    root = settings.artifact_storage_path.resolve()
    created = []
    try:
        if root.exists() and any(root.iterdir()):
            raise ValueError("Restore requires an empty artifact directory")
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > 1_000_000_000:
                raise ValueError("Backup exceeds the supported restore size")
            names = set()
            files = []
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                if (
                    name in names
                    or "\\" in name
                    or ":" in name
                    or ".." in parts
                    or PurePosixPath(name).is_absolute()
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.file_size > 50_000_000
                ):
                    raise ValueError("Unsafe archive path or entry size")
                names.add(name)
                if name.startswith("artifacts/") and not entry.is_dir():
                    path = (root / name.removeprefix("artifacts/")).resolve()
                    if root not in path.parents:
                        raise ValueError("Unsafe archive path")
                    files.append((entry, path))
                elif name not in {"records.json", "backup.json"}:
                    raise ValueError("Unexpected archive entry")
            metadata = json.loads(archive.read("backup.json"))
            if metadata.get("schema_version") != 1:
                raise ValueError("Unsupported backup schema")
            records = json.loads(archive.read("records.json"))
            columns = set(database.records.c.keys())
            if not isinstance(records, list) or any(
                not isinstance(row, dict) or set(row) != columns for row in records
            ):
                raise ValueError("Invalid record snapshot")
            with database.engine.begin() as conn:
                if conn.execute(select(database.records.c.key).limit(1)).first():
                    raise ValueError("Restore requires an empty database")
                if records:
                    conn.execute(database.records.insert(), records)
                for entry, path in files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        created.append(path)
                        stream.write(archive.read(entry))
    except Exception:
        # Only remove files created by this restore, each already resolved inside root.
        for path in reversed(created):
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        raise
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    restore(Settings(), args.input)
    print("Restored to empty target; external actions were not replayed.")
