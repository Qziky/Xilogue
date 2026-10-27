"""Consistent portable snapshots; restore validates before publishing a fresh directory."""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from xilogue.config import Config
from xilogue.storage.store import IMAGE_PATTERN, SCHEMA_VERSION

MAX_BACKUP = 8 * 1024**3
MAX_MEMBER = 2 * 1024**3


def sha_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def export_backup(store, output):
    """Caller must fence capture/start; SQLite and image enumeration share the store lock."""
    output = Path(output).resolve()
    if output.is_relative_to(store.root):
        raise ValueError("backup_must_be_outside_data_directory")
    if output.exists():
        raise ValueError("backup_destination_exists")
    descriptor, filename = tempfile.mkstemp(
        prefix=".xilogue-backup-", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(filename)
    try:
        with store.lock, tempfile.TemporaryDirectory(prefix="xilogue-backup-") as scratch:
            database = Path(scratch) / "memory.sqlite3"
            connection = sqlite3.connect(database)
            try:
                store.db.backup(connection)
            finally:
                connection.close()
            entries = {
                "memory.sqlite3": database,
                ".xilogue": store.root / ".xilogue",
            }
            for row in store.rows(True):
                if row["storage_state"] == "ready":
                    image = store.image_path(row["image_path"])
                    if not image.is_file():
                        raise ValueError("backup_source_image_missing")
                    entries[row["image_path"]] = image
            configuration = Path(scratch) / "config.json"
            backup_config = store.config.to_dict()
            for prefix in ("ai", "embedding"):
                backup_config[prefix + "_remote_consent"] = False
            configuration.write_text(
                json.dumps(backup_config, ensure_ascii=False), encoding="utf-8"
            )
            entries["config.json"] = configuration
            total = sum(p.stat().st_size for p in entries.values())
            if total > MAX_BACKUP or any(p.stat().st_size > MAX_MEMBER for p in entries.values()):
                raise ValueError("backup_limit_exceeded")
            manifest = {
                "format": 1,
                "schema": SCHEMA_VERSION,
                "created_at_utc": datetime.now(UTC).isoformat(),
                "files": {
                    k: {"size": p.stat().st_size, "sha256": sha_file(p)} for k, p in entries.items()
                },
            }
            with (
                temporary.open("wb") as stream,
                zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive,
            ):
                for name, path in entries.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", json.dumps(manifest))
            os.rename(temporary, output)
            return manifest
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(archive_path, destination):
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore_requires_new_directory")
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    stage = Path(tempfile.mkdtemp(prefix=".xilogue-restore-", dir=parent)).resolve()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.infolist()
            names = [i.filename for i in info]
            if (
                len(info) > 100000
                or len(names) != len(set(names))
                or sum(i.file_size for i in info) > MAX_BACKUP
            ):
                raise ValueError("backup_archive_limit")
            for item in info:
                name = item.filename
                allowed = name in (
                    "manifest.json",
                    "config.json",
                    ".xilogue",
                    "memory.sqlite3",
                ) or bool(IMAGE_PATTERN.fullmatch(name) and not name.endswith(".tmp"))
                if (
                    not allowed
                    or "\\" in name
                    or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.is_dir()
                    or item.file_size > MAX_MEMBER
                ):
                    raise ValueError("unsafe_backup_member")
            if (
                "manifest.json" not in names
                or archive.getinfo("manifest.json").file_size > 32 * 1024**2
            ):
                raise ValueError("backup_manifest_missing_or_large")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format") != 1
                or type(manifest.get("schema")) is not int
                or not 1 <= manifest["schema"] <= SCHEMA_VERSION
                or not isinstance(manifest.get("files"), dict)
                or set(manifest["files"]) != set(names) - {"manifest.json"}
                or not {"config.json", ".xilogue", "memory.sqlite3"} <= set(names)
            ):
                raise ValueError("backup_manifest_invalid")
            for item in info:
                if item.filename == "manifest.json":
                    continue
                metadata = manifest["files"][item.filename]
                path = stage / item.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                if metadata != {"size": path.stat().st_size, "sha256": sha_file(path)}:
                    raise ValueError("backup_hash_mismatch")
        if (stage / ".xilogue").read_text(encoding="utf-8") != "Xilogue/v1\n":
            raise ValueError("backup_marker_invalid")
        Config.from_dict(json.loads((stage / "config.json").read_text(encoding="utf-8")))
        connection = sqlite3.connect(f"{(stage / 'memory.sqlite3').as_uri()}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if (
                version != manifest["schema"]
                or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("backup_database_invalid")
            for relative, state in connection.execute(
                "SELECT image_path,storage_state FROM observations"
            ):
                if not IMAGE_PATTERN.fullmatch(relative) or (
                    state == "ready" and relative not in manifest["files"]
                ):
                    raise ValueError("backup_image_reference_invalid")
        finally:
            connection.close()
        if destination.exists():
            raise ValueError("restore_destination_changed")
        os.rename(stage, destination)
        return {
            "restored_to": str(destination),
            "schema": manifest["schema"],
            "files": len(manifest["files"]),
        }
    finally:
        if stage.exists() and stage.parent == parent and stage.name.startswith(".xilogue-restore-"):
            shutil.rmtree(stage)
