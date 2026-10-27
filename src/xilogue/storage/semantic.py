"""Optional local vectors, explicit cosine evidence, and cross-day topic links."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from xilogue.processing.ocr_types import normalize_text


def unit_vector(values):
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 4096
        or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e10 for x in values)
    ):
        raise ValueError("invalid_embedding")
    norm = math.sqrt(sum(x * x for x in values))
    if norm < 1e-12:
        raise ValueError("empty_embedding")
    return [x / norm for x in values]


def migrate_semantic(db):
    for statement in (
        """CREATE TABLE IF NOT EXISTS embedding_jobs (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
            token TEXT,next_attempt_utc TEXT NOT NULL,last_error TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS session_vectors (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,model TEXT NOT NULL,digest TEXT NOT NULL,
            vector_json TEXT NOT NULL,text_sha256 TEXT NOT NULL,created_at_utc TEXT NOT NULL)""",
        """CREATE TRIGGER IF NOT EXISTS vectors_revision_changed
            AFTER UPDATE OF revision ON sessions
            WHEN new.revision!=old.revision BEGIN
            DELETE FROM session_vectors WHERE session_id=new.id;
            UPDATE embedding_jobs SET revision=new.revision,status='pending',attempts=0,token=NULL,
            next_attempt_utc=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
            WHERE session_id=new.id; END""",
    ):
        db.execute(statement)
    db.execute(
        "UPDATE embedding_jobs SET status='pending',token=NULL,attempts=max(0,attempts-1) "
        "WHERE status='running'"
    )


class SemanticStorage:
    def queue_embeddings(self):
        self.sync_sessions()
        with self.lock, self.db:
            queued = 0
            for row in self.db.execute(
                "SELECT id,revision FROM sessions ORDER BY started_at_utc DESC LIMIT 1000"
            ).fetchall():
                if not self.analysis_evidence(row["id"]):
                    continue
                self.db.execute(
                    "INSERT OR REPLACE INTO embedding_jobs VALUES (?,?,'pending',0,NULL,?,'')",
                    (row["id"], row["revision"], datetime.now(UTC).isoformat()),
                )
                queued += 1
            return {"queued": queued}

    def claim_embedding(self, cutoff=None, *, automatic=False):
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
                """SELECT * FROM embedding_jobs WHERE status IN ('pending','failed') AND attempts<3
                AND next_attempt_utc<=?"""
                + clause
                + " ORDER BY next_attempt_utc,session_id LIMIT 1",
                ((cutoff or datetime.now(UTC)).isoformat(), *(stable or [])),
            ).fetchone()
            if row is None:
                return None
            evidence = self.analysis_evidence(row["session_id"])
            text = "\n".join(e["text"] for e in evidence)
            job = dict(row) | {
                "kind": "embedding",
                "token": uuid4().hex,
                "evidence": evidence,
                "text": text,
            }
            self.db.execute(
                "UPDATE embedding_jobs SET status='running',attempts=attempts+1,token=? "
                "WHERE session_id=?",
                (job["token"], job["session_id"]),
            )
            return job

    def release_embedding(self, job, error=None):
        due = (
            datetime.now(UTC) + timedelta(seconds=min(60, 5 * 2 ** job["attempts"]))
            if error
            else datetime.fromisoformat(job["next_attempt_utc"])
        )
        with self.lock, self.db:
            self.db.execute(
                """UPDATE embedding_jobs SET status=?,token=NULL,attempts=attempts-?,last_error=?,
                next_attempt_utc=? WHERE session_id=? AND token=? AND status='running'""",
                (
                    "failed" if error else "pending",
                    0 if error else 1,
                    error or "",
                    due.isoformat(),
                    job["session_id"],
                    job["token"],
                ),
            )

    def finish_embedding(self, job, vector, model, digest):
        vector = unit_vector(vector)
        with self.lock, self.db:
            session = self.session(job["session_id"])
            valid = self.db.execute(
                "SELECT 1 FROM embedding_jobs WHERE session_id=? AND token=? AND status='running'",
                (job["session_id"], job["token"]),
            ).fetchone()
            if session is None or valid is None or session["revision"] != job["revision"]:
                return False
            self.db.execute(
                "INSERT OR REPLACE INTO session_vectors VALUES (?,?,?,?,?,?,?)",
                (
                    job["session_id"],
                    job["revision"],
                    model,
                    digest,
                    json.dumps(vector),
                    hashlib.sha256(job["text"].encode()).hexdigest(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self.db.execute(
                "UPDATE embedding_jobs SET status='done',token=NULL,last_error='' "
                "WHERE session_id=?",
                (job["session_id"],),
            )
            self.fault("before_embedding_commit")
            return True

    def embedding_stats(self):
        with self.lock:
            result = dict.fromkeys(("pending", "running", "done", "failed"), 0)
            result.update(
                dict(self.db.execute("SELECT status,count(*) FROM embedding_jobs GROUP BY status"))
            )
            return result

    def related(self, query, *, vector=None, model=None, digest=None, limit=30, app=None):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 512
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid_related_query")
        query = normalize_text(query.strip())
        terms = set(re.findall(r"[a-z0-9_]{2,40}", query))
        chinese = "".join(re.findall(r"[\u3400-\u9fff]", query))
        terms.update(chinese[i : i + 2] for i in range(min(len(chinese) - 1, 24)))
        if not terms:
            terms = {query}
        if vector is not None:
            vector = unit_vector(vector)
        self.sync_sessions()
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM sessions ORDER BY started_at_utc DESC LIMIT 3000"
            ).fetchall()
            hits = []
            for row in rows:
                session = self.session(row[0], limit=1)
                if session is None or (
                    app and normalize_text(session["app_name"]) != normalize_text(app)
                ):
                    continue
                evidence = self.analysis_evidence(row[0])
                body = normalize_text("\n".join(e["text"] for e in evidence))
                matches = [t for t in terms if t in body]
                lexical = len(matches) / len(terms)
                similarity = None
                if vector is not None:
                    saved = self.db.execute(
                        "SELECT vector_json FROM session_vectors "
                        "WHERE session_id=? AND revision=? AND model=? AND digest=?",
                        (row[0], session["revision"], model, digest),
                    ).fetchone()
                    if saved:
                        other = json.loads(saved[0])
                        if len(other) == len(vector):
                            similarity = sum(a * b for a, b in zip(other, vector, strict=True))
                if not matches and (similarity is None or similarity < 0.3):
                    continue
                score = (
                    lexical if similarity is None else 0.25 * lexical + 0.75 * max(0, similarity)
                )
                hits.append(
                    {
                        k: session[k]
                        for k in ("id", "title", "app_name", "representative_id", "started_at_utc")
                    }
                    | {
                        "score": score,
                        "matched_terms": sorted(matches),
                        "cosine": similarity,
                        "basis": "local_vector_and_keywords"
                        if similarity is not None
                        else "related_keywords",
                    }
                )
            hits.sort(key=lambda h: (h["score"], h["started_at_utc"]), reverse=True)
            return {"results": hits[:limit], "scanned_sessions": len(rows), "scan_limit": 3000}

    def topics(self, timezone="UTC"):
        from xilogue.storage.reviews import day_window

        day_window("2026-01-01", timezone)
        zone = ZoneInfo(timezone)
        with self.lock:
            result = {}
            muted = self.muted_topics()
            rows = self.db.execute(
                "SELECT session_id FROM insights ORDER BY created_at_utc DESC LIMIT 1000"
            ).fetchall()
            for row in rows:
                insight = self.insight(row[0])
                session = self.session(row[0], limit=1)
                if insight is None or session is None:
                    continue
                feedback = self.db.execute(
                    "SELECT kind,hidden FROM review_feedback WHERE session_id=?", (row[0],)
                ).fetchone()
                if feedback and (feedback[1] or feedback[0] in ("never", "useless", "mastered")):
                    continue
                days = set()
                for claim in insight["payload"]["claims"]:
                    source = self.observation(claim["source_id"])
                    if source:
                        days.add(
                            datetime.fromisoformat(source["captured_at_utc"])
                            .astimezone(zone)
                            .date()
                            .isoformat()
                        )
                if not days:
                    continue
                for topic in dict.fromkeys(insight["payload"]["topics"]):
                    key = normalize_text(topic)
                    if key in muted:
                        continue
                    group = result.setdefault(key, {"topic": topic, "days": set(), "sessions": []})
                    group["days"].update(days)
                    group["sessions"].append(
                        {
                            "id": row[0],
                            "day": min(days),
                            "days": sorted(days),
                            "title": session["title"],
                        }
                    )
            return [
                {**value, "days": sorted(value["days"]), "sessions": value["sessions"][:100]}
                for value in sorted(
                    result.values(), key=lambda g: len(g["sessions"]), reverse=True
                )[:100]
            ]
