"""Durable OCR leases, derived-data deletion and literal substring retrieval."""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from xilogue.processing.ocr_types import normalize_text, validate_document
from xilogue.storage.records import RecordStorage, filters


def migrate(db):
    # Caller owns one explicit transaction, including the schema version update.
    statements = [
        """CREATE TABLE IF NOT EXISTS processing_jobs (
            observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
            attempts INTEGER NOT NULL DEFAULT 0, token TEXT,
            next_attempt_utc TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
            updated_at_utc TEXT NOT NULL)""",
        """CREATE INDEX IF NOT EXISTS jobs_available
            ON processing_jobs(status, next_attempt_utc)""",
        """CREATE TABLE IF NOT EXISTS ocr_results (
            observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
            text TEXT NOT NULL, normalized TEXT NOT NULL, lines_json TEXT NOT NULL,
            engine TEXT NOT NULL, created_at_utc TEXT NOT NULL)""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
            observation_id UNINDEXED, normalized, tokenize='trigram')""",
        """CREATE TRIGGER IF NOT EXISTS ocr_insert AFTER INSERT ON ocr_results BEGIN
            INSERT INTO ocr_fts(observation_id, normalized)
            VALUES (new.observation_id, new.normalized); END""",
        """CREATE TRIGGER IF NOT EXISTS ocr_delete AFTER DELETE ON ocr_results BEGIN
            DELETE FROM ocr_fts WHERE observation_id=old.observation_id; END""",
        """CREATE TRIGGER IF NOT EXISTS source_invalidated
            AFTER UPDATE OF storage_state ON observations WHEN new.storage_state!='ready' BEGIN
            DELETE FROM ocr_results WHERE observation_id=new.id;
            DELETE FROM processing_jobs WHERE observation_id=new.id; END""",
        """CREATE TRIGGER IF NOT EXISTS source_created AFTER INSERT ON observations
            WHEN new.storage_state='ready' BEGIN
            INSERT INTO processing_jobs(observation_id,status,next_attempt_utc,updated_at_utc)
            VALUES (new.id,'pending',new.created_at_utc,new.created_at_utc); END""",
    ]
    for statement in statements:
        db.execute(statement)
    now = datetime.now(UTC).isoformat()
    db.execute(
        """INSERT OR IGNORE INTO processing_jobs
           (observation_id,status,next_attempt_utc,updated_at_utc)
           SELECT id,'pending',?,? FROM observations WHERE storage_state='ready'
           AND id NOT IN (SELECT observation_id FROM ocr_results)""",
        (now, now),
    )
    db.execute(
        """UPDATE processing_jobs SET status='pending', token=NULL,
           attempts=max(0,attempts-1), next_attempt_utc=?, updated_at_utc=?
           WHERE status='running'""",
        (now, now),
    )


class OcrStorage(RecordStorage):
    def latest_observation(self):
        with self.lock:
            page = self.observations_page(limit=1)["results"]
            return self.observation(page[0]["id"]) if page else None

    def observation(self, ident):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM observations WHERE id=? AND storage_state='ready'", (ident,)
            ).fetchone()
            if row is None or not self.image_path(row["image_path"]).is_file():
                return None
            return dict(row)

    def claim_ocr(self, now=None):
        at = (now or datetime.now(UTC)).isoformat()
        with self.lock, self.db:
            row = self.db.execute(
                """SELECT o.id,o.image_path,o.width,o.height,j.attempts FROM observations o
                   JOIN processing_jobs j ON o.id=j.observation_id
                   WHERE o.storage_state='ready' AND j.status IN ('pending','failed')
                   AND j.attempts<3 AND j.next_attempt_utc<=?
                   ORDER BY o.captured_at_utc,o.id LIMIT 1""",
                (at,),
            ).fetchone()
            if row is None:
                return None
            job = dict(row) | {"token": uuid4().hex}
            self.db.execute(
                """UPDATE processing_jobs SET status='running',attempts=attempts+1,
                   token=?,updated_at_utc=? WHERE observation_id=?""",
                (job["token"], at, job["id"]),
            )
            return job

    def release_ocr(self, job, error=None, now=None):
        now = now or datetime.now(UTC)
        retry = now + timedelta(seconds=min(60, 5 * 2 ** job["attempts"])) if error else now
        with self.lock, self.db:
            self.db.execute(
                """UPDATE processing_jobs SET status=?,token=NULL,last_error=?,
                   next_attempt_utc=?,updated_at_utc=?,attempts=attempts-?
                   WHERE observation_id=? AND token=? AND status='running'""",
                (
                    "failed" if error else "pending",
                    error or "",
                    retry.isoformat(),
                    now.isoformat(),
                    0 if error else 1,
                    job["id"],
                    job["token"],
                ),
            )

    def finish_ocr(self, job, document):
        validate_document(document, job["width"], job["height"])
        with self.lock, self.db:
            row = self.db.execute(
                """SELECT o.image_path FROM observations o JOIN processing_jobs j
                   ON j.observation_id=o.id WHERE o.id=? AND o.storage_state='ready'
                   AND j.token=? AND j.status='running'""",
                (job["id"], job["token"]),
            ).fetchone()
            if row is None or not self.image_path(row["image_path"]).is_file():
                return False
            at = datetime.now(UTC).isoformat()
            self.db.execute(
                "INSERT INTO ocr_results VALUES (?,?,?,?,?,?)",
                (
                    job["id"],
                    document.text,
                    normalize_text(document.text),
                    json.dumps([asdict(line) for line in document.lines], ensure_ascii=False),
                    document.engine,
                    at,
                ),
            )
            self.db.execute(
                """UPDATE processing_jobs SET status='done',token=NULL,last_error='',
                   updated_at_utc=? WHERE observation_id=?""",
                (at, job["id"]),
            )
            self.fault("before_ocr_commit")
            return True

    def retry_ocr(self, ident=None):
        with self.lock, self.db:
            if ident is not None and self.observation(ident) is None:
                return 0
            now = datetime.now(UTC).isoformat()
            return self.db.execute(
                """UPDATE processing_jobs SET status='pending',attempts=0,last_error='',
                   token=NULL,next_attempt_utc=?,updated_at_utc=? WHERE status='failed'"""
                + (" AND observation_id=?" if ident is not None else ""),
                (now, now, ident) if ident is not None else (now, now),
            ).rowcount

    def ocr_stats(self):
        with self.lock:
            counts = dict.fromkeys(("pending", "running", "done", "failed"), 0)
            counts.update(
                dict(
                    self.db.execute(
                        "SELECT status,count(*) FROM processing_jobs GROUP BY status"
                    ).fetchall()
                )
            )
            return counts

    def document(self, ident):
        with self.lock:
            row = self.db.execute(
                """SELECT o.*,r.text,r.lines_json,r.engine FROM observations o
                   JOIN ocr_results r ON r.observation_id=o.id
                   WHERE o.id=? AND o.storage_state='ready'""",
                (ident,),
            ).fetchone()
            if row is None or not self.image_path(row["image_path"]).is_file():
                return None
            return dict(row)

    def search(self, query, *, limit=100, app=None, after=None, before=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("query_must_contain_1_to_512_characters")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_must_be_1_to_500")
        query = normalize_text(query.strip())
        where = ["o.storage_state='ready'", "instr(r.normalized,?)>0"]
        values = [query]
        if len(query) >= 3:
            where.append("o.id IN (SELECT observation_id FROM ocr_fts WHERE ocr_fts MATCH ?)")
            values.append('"' + query.replace('"', '""') + '"')
        conditions, params = filters(app, after, before)
        where.extend(conditions)
        values.extend(params)
        with self.lock:
            rows = self.db.execute(
                """SELECT o.id,o.app_name,o.window_title,o.captured_at_utc,o.image_path,
                   substr(r.text,max(1,instr(r.normalized,?)-60),180) AS preview
                   FROM observations o JOIN ocr_results r ON r.observation_id=o.id
                   WHERE """
                + " AND ".join(where)
                + " ORDER BY o.captured_at_utc DESC,o.id LIMIT ?",
                (query, *values, limit),
            ).fetchall()
            return [dict(row) for row in rows if self.image_path(row["image_path"]).is_file()]
