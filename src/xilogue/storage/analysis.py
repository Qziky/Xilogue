"""Leased Session analysis; source/revision changes invalidate all dependent results."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from xilogue.processing.analysis_types import validate_analysis


def migrate_analysis(db):
    for statement in (
        """CREATE TABLE IF NOT EXISTS analysis_jobs (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, token TEXT, next_attempt_utc TEXT NOT NULL,
            last_error TEXT NOT NULL DEFAULT '', updated_at_utc TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS insights (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, payload_json TEXT NOT NULL, model TEXT NOT NULL,
            created_at_utc TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS insight_sources (
            session_id TEXT NOT NULL REFERENCES insights(session_id) ON DELETE CASCADE,
            observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
            PRIMARY KEY(session_id,observation_id))""",
        """CREATE TRIGGER IF NOT EXISTS analysis_revision_changed
            AFTER UPDATE OF revision ON sessions
            WHEN new.revision!=old.revision BEGIN
            DELETE FROM insights WHERE session_id=new.id;
            UPDATE analysis_jobs SET revision=new.revision,status='pending',attempts=0,token=NULL,
              next_attempt_utc=strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),last_error=''
              WHERE session_id=new.id; END""",
    ):
        db.execute(statement)
    db.execute(
        "UPDATE analysis_jobs SET status='pending',token=NULL,attempts=max(0,attempts-1) "
        "WHERE status='running'"
    )


class AnalysisStorage:
    def stable_sessions(self, now=None):
        cutoff = (
            (now or datetime.now(UTC)) - timedelta(seconds=self.config.ai_stable_seconds)
        ).isoformat()
        return [
            r[0]
            for r in self.db.execute(
                """SELECT s.id FROM sessions s
            WHERE s.ended_at_utc<=? AND NOT EXISTS (
              SELECT 1 FROM session_items i JOIN processing_jobs j
              ON i.observation_id=j.observation_id
              WHERE i.session_id=s.id AND (j.status IN ('pending','running')
              OR (j.status='failed' AND j.attempts<3)))
            ORDER BY s.started_at_utc DESC LIMIT 1000""",
                (cutoff,),
            )
        ]

    def queue_automatic(self, now=None):
        self.sync_sessions()
        with self.lock, self.db:
            for ident in self.stable_sessions(now):
                need_analysis = (
                    self.config.ai_enabled
                    and not self.db.execute(
                        "SELECT 1 FROM analysis_jobs WHERE session_id=? UNION ALL "
                        "SELECT 1 FROM insights WHERE session_id=?",
                        (ident, ident),
                    ).fetchone()
                )
                need_vector = (
                    self.config.semantic_enabled
                    and not self.db.execute(
                        "SELECT 1 FROM embedding_jobs WHERE session_id=? UNION ALL "
                        "SELECT 1 FROM session_vectors WHERE session_id=?",
                        (ident, ident),
                    ).fetchone()
                )
                if not need_analysis and not need_vector:
                    continue
                if not self.analysis_evidence(ident):
                    continue
                revision = self.db.execute(
                    "SELECT revision FROM sessions WHERE id=?", (ident,)
                ).fetchone()[0]
                at = datetime.now(UTC).isoformat()
                if need_analysis:
                    self.db.execute(
                        "INSERT OR IGNORE INTO analysis_jobs VALUES (?,?,'pending',0,NULL,?,'',?)",
                        (ident, revision, at, at),
                    )
                if need_vector:
                    self.db.execute(
                        "INSERT OR IGNORE INTO embedding_jobs VALUES (?,?,'pending',0,NULL,?,'')",
                        (ident, revision, at),
                    )

    def analysis_evidence(self, ident):
        with self.lock:
            if self.session(ident) is None:
                return []
            rows = self.db.execute(
                """SELECT o.id,o.captured_at_utc,o.app_name,substr(o.window_title,1,80) AS title,
                   substr(r.text,1,600) AS text FROM observations o
                   JOIN session_items i ON i.observation_id=o.id
                   JOIN ocr_results r ON r.observation_id=o.id
                   WHERE i.session_id=? AND o.storage_state='ready' AND trim(r.text)!=''
                   ORDER BY o.captured_at_utc DESC,o.id DESC LIMIT 8""",
                (ident,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def queue_analysis(self, ids=None):
        self.sync_sessions()
        if ids is not None and (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 50
            or any(not isinstance(i, str) for i in ids)
        ):
            raise ValueError("invalid_analysis_selection")
        with self.lock, self.db:
            if ids is None:
                ids = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT id FROM sessions WHERE id NOT IN (SELECT session_id FROM insights) "
                        "ORDER BY started_at_utc DESC LIMIT 1000"
                    )
                ]
            queued = skipped = 0
            now = datetime.now(UTC).isoformat()
            for ident in ids:
                session = self.session(ident)
                if session is None or not self.analysis_evidence(ident):
                    skipped += 1
                    continue
                self.db.execute("DELETE FROM insights WHERE session_id=?", (ident,))
                self.db.execute(
                    """INSERT INTO analysis_jobs VALUES (?,?,'pending',0,NULL,?,'',?)
                    ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision,
                    status='pending',attempts=0,token=NULL,next_attempt_utc=excluded.next_attempt_utc,
                    last_error='',updated_at_utc=excluded.updated_at_utc""",
                    (ident, session["revision"], now, now),
                )
                queued += 1
            return {"queued": queued, "skipped": skipped}

    def claim_analysis(self, cutoff=None, *, automatic=False):
        with self.lock, self.db:
            stable = self.stable_sessions() if automatic else None
            if stable == []:
                return None
            clause = (
                (" AND session_id IN (" + ",".join("?" for _ in stable) + ")")
                if stable is not None
                else ""
            )
            row = self.db.execute(
                """SELECT * FROM analysis_jobs WHERE status IN ('pending','failed') AND attempts<3
                AND next_attempt_utc<=?"""
                + clause
                + " ORDER BY next_attempt_utc,session_id LIMIT 1",
                ((cutoff or datetime.now(UTC)).isoformat(), *(stable or [])),
            ).fetchone()
            if row is None:
                return None
            job = dict(row) | {
                "token": uuid4().hex,
                "evidence": self.analysis_evidence(row["session_id"]),
            }
            self.db.execute(
                "UPDATE analysis_jobs SET status='running',attempts=attempts+1,token=? "
                "WHERE session_id=?",
                (job["token"], job["session_id"]),
            )
            return job

    def release_analysis(self, job, error=None):
        now = datetime.now(UTC)
        due = (
            now + timedelta(seconds=min(60, 5 * 2 ** job["attempts"]))
            if error
            else datetime.fromisoformat(job["next_attempt_utc"])
        )
        with self.lock, self.db:
            self.db.execute(
                """UPDATE analysis_jobs SET status=?,token=NULL,attempts=attempts-?,last_error=?,
                next_attempt_utc=?,updated_at_utc=?
                WHERE session_id=? AND token=? AND status='running'""",
                (
                    "failed" if error else "pending",
                    0 if error else 1,
                    error or "",
                    due.isoformat(),
                    now.isoformat(),
                    job["session_id"],
                    job["token"],
                ),
            )

    def finish_analysis(self, job, payload, model):
        payload = validate_analysis(payload, job["evidence"])
        with self.lock, self.db:
            session = self.session(job["session_id"])
            row = self.db.execute(
                "SELECT * FROM analysis_jobs WHERE session_id=? AND token=? AND status='running'",
                (job["session_id"], job["token"]),
            ).fetchone()
            if session is None or row is None or session["revision"] != job["revision"]:
                return False
            self.db.execute(
                "INSERT INTO insights VALUES (?,?,?,?,?)",
                (
                    job["session_id"],
                    job["revision"],
                    json.dumps(payload, ensure_ascii=False),
                    model,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self.db.executemany(
                "INSERT INTO insight_sources VALUES (?,?)",
                [
                    (job["session_id"], ident)
                    for ident in {c["source_id"] for c in payload["claims"]}
                ],
            )
            self.db.execute(
                "UPDATE analysis_jobs SET status='done',token=NULL,last_error='' "
                "WHERE session_id=?",
                (job["session_id"],),
            )
            self.fault("before_analysis_commit")
            return True

    def insight(self, ident):
        with self.lock:
            session = self.session(ident)
            if session is None:
                return None
            row = self.db.execute(
                "SELECT * FROM insights WHERE session_id=? AND revision=?",
                (ident, session["revision"]),
            ).fetchone()
            return dict(row) | {"payload": json.loads(row["payload_json"])} if row else None

    def analysis_stats(self):
        with self.lock:
            result = dict.fromkeys(("pending", "running", "done", "failed"), 0)
            result.update(
                dict(self.db.execute("SELECT status,count(*) FROM analysis_jobs GROUP BY status"))
            )
            return result
