"""SQLite + independent WebP files, compensating writes and retryable deletion."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2

from xilogue.config import Config
from xilogue.storage.analysis import AnalysisStorage, migrate_analysis
from xilogue.storage.locking import DataLock
from xilogue.storage.ocr import OcrStorage, migrate
from xilogue.storage.reviews import ReviewStorage, migrate_reviews
from xilogue.storage.semantic import SemanticStorage, migrate_semantic
from xilogue.storage.sessions import SessionStorage, migrate_sessions

IMAGE_PATTERN = re.compile(r"images/\d{4}-\d{2}-\d{2}/\d{8}T\d{6}_[a-f0-9]{32}\.webp(?:\.tmp)?$")


class StorageError(RuntimeError):
    pass


def encode_webp(pixels, quality):
    ok, array = cv2.imencode(".webp", pixels, [cv2.IMWRITE_WEBP_QUALITY, quality])
    if not ok:
        raise StorageError("encode_failed")
    return array.tobytes()


SCHEMA_VERSION = 8


class Store(OcrStorage, SessionStorage, AnalysisStorage, ReviewStorage, SemanticStorage):
    def __init__(self, root: Path, config: Config, fault=None):
        self.root, self.config = root.resolve(), config
        self.lock = threading.RLock()
        self.fault = fault or (lambda stage: None)
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / ".xilogue"
        if marker.exists():
            if marker.is_symlink() or marker.read_text(encoding="utf-8") != "Xilogue/v1\n":
                raise StorageError("data_directory_marker_invalid")
        else:
            if any(x.name not in ("config.json", ".instance.lock") for x in self.root.iterdir()):
                raise StorageError("data_directory_not_empty")
            marker.write_text("Xilogue/v1\n", encoding="utf-8")
        self.images = self.root / "images"
        self._inside(self.images).mkdir(exist_ok=True)
        self.database = self._inside(self.root / "memory.sqlite3")
        self._instance_lock = DataLock(self._inside(self.root / ".instance.lock"))
        try:
            self.db = sqlite3.connect(self.database, check_same_thread=False, timeout=10)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in range(SCHEMA_VERSION + 1):
                raise StorageError("unsupported_schema_version")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("""CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, captured_at_utc TEXT NOT NULL,
                created_at_utc TEXT NOT NULL, source_type TEXT NOT NULL,
                app_name TEXT NOT NULL, process_id INTEGER NOT NULL,
                window_title TEXT NOT NULL, capture_backend TEXT NOT NULL,
                image_path TEXT UNIQUE NOT NULL, image_bytes INTEGER NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL,
                capture_reason TEXT NOT NULL, visual_metrics_json TEXT NOT NULL,
                storage_state TEXT NOT NULL CHECK(storage_state IN ('ready','deleting','missing'))
            )""")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS observation_age ON observations(captured_at_utc)"
            )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS observation_page ON observations(captured_at_utc,id)"
            )
            migrate(self.db)
            migrate_sessions(self.db)
            migrate_analysis(self.db)
            migrate_reviews(self.db)
            migrate_semantic(self.db)
            self.db.execute("""CREATE TABLE IF NOT EXISTS browser_pages (
                observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
                url TEXT NOT NULL,title TEXT NOT NULL,truncated INTEGER NOT NULL,
                viewport_width INTEGER NOT NULL,viewport_height INTEGER NOT NULL)""")
            self.db.execute("""CREATE TRIGGER IF NOT EXISTS browser_invalidated
                AFTER UPDATE OF storage_state ON observations WHEN new.storage_state!='ready'
                BEGIN DELETE FROM browser_pages WHERE observation_id=new.id; END""")
            if "nearest_desktop_id" not in {
                r[1] for r in self.db.execute("PRAGMA table_info(browser_pages)")
            }:
                self.db.execute(
                    "ALTER TABLE browser_pages ADD COLUMN nearest_desktop_id TEXT "
                    "REFERENCES observations(id) ON DELETE SET NULL"
                )
            self.fault("before_schema_commit")
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.db.commit()
            self.db.row_factory = sqlite3.Row
            self.recovery = self.recover()
            self.maintenance()
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._instance_lock.close()
            raise

    def _inside(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise StorageError("unsafe_storage_path")
        return resolved

    def image_path(self, relative: str) -> Path:
        if not IMAGE_PATTERN.fullmatch(relative):
            raise StorageError("invalid_image_path")
        return self._inside(self.root / relative)

    def _unlink(self, path):
        self._inside(path).unlink(missing_ok=True)

    def rows(self, include_incomplete=False):
        with self.lock:
            where = "" if include_incomplete else "WHERE storage_state='ready'"
            return [
                dict(r)
                for r in self.db.execute(
                    f"SELECT * FROM observations {where} ORDER BY captured_at_utc, id"
                )
            ]

    def _delete(self, row):
        with self.db:
            self.db.execute(
                "UPDATE observations SET storage_state='deleting' WHERE id=?", (row["id"],)
            )
        self.fault("delete_marked")
        self._unlink(self.image_path(row["image_path"]))
        self.fault("delete_file_removed")
        with self.db:
            self.db.execute("DELETE FROM observations WHERE id=?", (row["id"],))

    def delete_observation(self, ident):
        """Invalidate derived data first; physical cleanup can be retried by maintenance."""
        with self.lock:
            row = self.db.execute("SELECT * FROM observations WHERE id=?", (ident,)).fetchone()
            if row is None:
                return {"status": "not_found"}
            try:
                self._delete(row)
            except (OSError, sqlite3.Error, StorageError):
                state = self.db.execute(
                    "SELECT storage_state FROM observations WHERE id=?", (ident,)
                ).fetchone()
                if state is not None and state[0] == "deleting":
                    return {"status": "cleanup_pending"}
                raise
            return {"status": "deleted"}

    def recover(self):
        report = {
            "temporary_removed": 0,
            "orphan_removed": 0,
            "missing": 0,
            "deleted": 0,
            "errors": 0,
        }
        with self.lock:
            rows = self.rows(True)
            referenced = {r["image_path"] for r in rows}
            for row in rows:
                try:
                    path = self.image_path(row["image_path"])
                    if row["storage_state"] == "deleting":
                        self._delete(row)
                        report["deleted"] += 1
                    elif not path.is_file():
                        with self.db:
                            self.db.execute(
                                "UPDATE observations SET storage_state='missing' WHERE id=?",
                                (row["id"],),
                            )
                        report["missing"] += 1
                    elif row["storage_state"] == "missing":
                        # Do not silently resurrect a record by placing a file at its path.
                        report["missing"] += 1
                except (OSError, sqlite3.Error, StorageError):
                    report["errors"] += 1
            for path in self.images.glob("*/*"):
                relative = path.relative_to(self.root).as_posix()
                if not IMAGE_PATTERN.fullmatch(relative):
                    continue
                try:
                    if relative.endswith(".tmp"):
                        self._unlink(path)
                        report["temporary_removed"] += 1
                    elif relative not in referenced:
                        self._unlink(path)
                        report["orphan_removed"] += 1
                except (OSError, StorageError):
                    report["errors"] += 1
        return report

    def usage(self):
        return self.db.execute(
            "SELECT coalesce(sum(image_bytes),0) FROM observations WHERE storage_state != 'missing'"
        ).fetchone()[0]

    def maintenance(self, now=None, required=0):
        with self.lock:
            cutoff = (
                (now or datetime.now(UTC)) - timedelta(days=self.config.retention_days)
            ).isoformat()
            errors = 0
            quota = self.config.image_quota_mib * 1024 * 1024
            for row in self.rows(True):
                expired = row["captured_at_utc"] <= cutoff
                if expired or row["storage_state"] == "deleting" or self.usage() + required > quota:
                    try:
                        self._delete(row)
                    except (OSError, sqlite3.Error, StorageError):
                        errors += 1
            return {"errors": errors, "over_quota": self.usage() + required > quota}

    def save(
        self,
        frame,
        encoded: bytes,
        decision,
        validate=None,
        *,
        source_type="desktop_capture",
        on_insert=None,
    ):
        if len(encoded) > self.config.image_quota_mib * 1024 * 1024:
            raise StorageError("single_image_over_quota")
        with self.lock:
            maintenance = self.maintenance(required=len(encoded))
            if maintenance["over_quota"]:
                raise StorageError("quota_reclaim_failed")
            if validate is not None and not validate():
                raise StorageError("candidate_invalidated")
            now = datetime.now(UTC)
            captured = frame.captured_at.astimezone(UTC)
            ident = uuid4().hex
            relative = f"images/{captured:%Y-%m-%d}/{captured:%Y%m%dT%H%M%S}_{ident}.webp"
            path = self.image_path(relative)
            self._inside(path.parent).mkdir(exist_ok=True)
            temporary = path.with_suffix(".webp.tmp")
            try:
                self.fault("before_file_write")
                with temporary.open("xb") as file:
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                self.fault("before_file_replace")
                os.replace(temporary, path)
                self.fault("after_file_replace")
                if validate is not None and not validate():
                    raise StorageError("candidate_invalidated")
                with self.db:
                    self.db.execute(
                        "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            ident,
                            captured.isoformat(),
                            now.isoformat(),
                            source_type,
                            frame.target.app_name,
                            frame.target.pid,
                            frame.target.title,
                            frame.backend,
                            relative,
                            len(encoded),
                            frame.pixels.shape[1],
                            frame.pixels.shape[0],
                            decision.reason,
                            json.dumps(decision.metrics, sort_keys=True),
                            "ready",
                        ),
                    )
                    if on_insert is not None:
                        on_insert(ident, now.isoformat())
                    self.fault("before_db_commit")
            except BaseException:
                for item in (temporary, path):
                    try:
                        self._unlink(item)
                    except OSError:
                        pass  # A managed orphan is retried at recovery, never reported saved.
                raise
            return {
                "id": ident,
                "image_path": relative,
                "image_bytes": len(encoded),
                "captured_at_utc": captured.isoformat(),
            }

    def stats(self):
        with self.lock:
            sizes = {"image_bytes": 0, "temporary_bytes": 0, "database_bytes": 0}
            for path in self.images.glob("*/*"):
                if IMAGE_PATTERN.fullmatch(path.relative_to(self.root).as_posix()):
                    safe = self._inside(path)
                    if safe.is_file():
                        sizes["temporary_bytes" if safe.suffix == ".tmp" else "image_bytes"] += (
                            safe.stat().st_size
                        )
            for suffix in ("", "-wal", "-shm"):
                path = self._inside(Path(str(self.database) + suffix))
                if path.exists():
                    sizes["database_bytes"] += path.stat().st_size
            sizes["observations"] = self.db.execute(
                "SELECT count(*) FROM observations WHERE storage_state='ready'"
            ).fetchone()[0]
            sizes["incomplete"] = self.db.execute(
                "SELECT count(*) FROM observations WHERE storage_state!='ready'"
            ).fetchone()[0]
            return sizes

    def close(self):
        with self.lock:
            self.db.close()
            self._instance_lock.close()
