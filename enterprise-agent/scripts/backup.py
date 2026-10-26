"""Create a consistent record snapshot plus artifact files; never include credentials/.env."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from enterprise_agent.artifacts.models import StoredArtifact
from enterprise_agent.config import Settings
from enterprise_agent.storage.database import Database


def backup(settings: Settings, target: Path) -> Path:
    database = Database(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive archive creation prevents accidentally overwriting a previous backup.
    root = settings.artifact_storage_path.resolve()
    try:
        with database.engine.begin() as conn:
            records = [dict(row) for row in conn.execute(select(database.records)).mappings()]
            with ZipFile(target, "x", ZIP_DEFLATED) as archive:
                archive.writestr("records.json", json.dumps(records, ensure_ascii=False))
                archive.writestr(
                    "backup.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "created_at": datetime.now(UTC).isoformat(),
                            "settings_excluded": True,
                        }
                    ),
                )
                # Export only completed, integrity-checked artifacts and their manifests.
                for manifest in root.glob("*/*/*/manifest.json"):
                    stored = StoredArtifact.model_validate_json(manifest.read_bytes())
                    relative = manifest.relative_to(root)
                    if relative.parts[:3] != (
                        stored.tenant_key,
                        stored.user_key,
                        stored.record.artifact_id,
                    ):
                        raise ValueError("Artifact owner directory does not match its manifest")
                    content = manifest.parent / stored.record.filename
                    for file in (content, manifest):
                        if (
                            not file.is_file()
                            or file.is_symlink()
                            or any(parent.is_symlink() for parent in file.parents if parent != root)
                            or root not in file.resolve().parents
                        ):
                            raise ValueError("Unsafe artifact path")
                    if hashlib.sha256(content.read_bytes()).hexdigest() != stored.content_sha256:
                        raise ValueError("Artifact integrity check failed")
                    for file in (content, manifest):
                        archive.write(file, "artifacts/" + file.relative_to(root).as_posix())
    finally:
        database.engine.dispose()
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(backup(Settings(), args.output))
