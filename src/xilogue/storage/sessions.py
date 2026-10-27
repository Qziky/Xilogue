"""Recoverable activity grouping. Membership is durable; evidence remains in observations."""

from datetime import UTC, datetime
from difflib import SequenceMatcher
from uuid import uuid4

from xilogue.processing.ocr_types import normalize_text
from xilogue.storage.records import decode_cursor, encode_cursor


def migrate_sessions(db):
    statements = [
        """CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, app_name TEXT NOT NULL, title TEXT NOT NULL,
            started_at_utc TEXT NOT NULL, ended_at_utc TEXT NOT NULL,
            representative_id TEXT REFERENCES observations(id) ON DELETE SET NULL,
            manual INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
            created_at_utc TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS session_items (
            observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE)""",
        "CREATE INDEX IF NOT EXISTS session_members ON session_items(session_id)",
        "CREATE INDEX IF NOT EXISTS session_time ON sessions(started_at_utc,id)",
        """CREATE TRIGGER IF NOT EXISTS session_source_invalid BEFORE UPDATE OF storage_state
            ON observations WHEN new.storage_state!='ready' BEGIN
            DELETE FROM sessions WHERE id IN
              (SELECT session_id FROM session_items WHERE observation_id=old.id); END""",
        """CREATE TRIGGER IF NOT EXISTS session_source_deleted BEFORE DELETE ON observations BEGIN
            DELETE FROM sessions WHERE id IN
              (SELECT session_id FROM session_items WHERE observation_id=old.id); END""",
    ]
    for action in ("INSERT", "DELETE"):
        alias = "new" if action == "INSERT" else "old"
        statements.append(f"""CREATE TRIGGER IF NOT EXISTS session_ocr_{action.lower()}
            AFTER {action} ON ocr_results BEGIN
            UPDATE sessions SET revision=revision+1 WHERE id IN
              (SELECT session_id FROM session_items WHERE observation_id={alias}.observation_id);
            END""")
    for statement in statements:
        db.execute(statement)


class SessionStorage:
    def _new_session(self, rows, manual=False):
        ident = uuid4().hex
        apps = {r["app_name"] for r in rows}
        self.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ident,
                next(iter(apps)) if len(apps) == 1 else "mixed",
                rows[0]["window_title"],
                rows[0]["captured_at_utc"],
                rows[-1]["captured_at_utc"],
                rows[-1]["id"],
                int(manual),
                1,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.executemany(
            "INSERT INTO session_items VALUES (?,?)", [(r["id"], ident) for r in rows]
        )
        return ident

    def sync_sessions(self, limit=2000):
        """Bound work; unassigned observations are the durable continuation cursor."""
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("invalid_grouping_batch_size")
        with self.lock, self.db:
            rows = self.db.execute(
                """SELECT o.* FROM observations o WHERE storage_state='ready'
                AND NOT EXISTS (SELECT 1 FROM session_items i WHERE i.observation_id=o.id)
                ORDER BY captured_at_utc,id LIMIT ?""",
                (limit,),
            ).fetchall()
            added = 0
            for row in rows:
                if not self.image_path(row["image_path"]).is_file():
                    self.db.execute(
                        "UPDATE observations SET storage_state='missing' WHERE id=?", (row["id"],)
                    )
                    continue
                previous = self.db.execute(
                    "SELECT * FROM sessions WHERE ended_at_utc<=? "
                    "ORDER BY ended_at_utc DESC,id DESC LIMIT 1",
                    (row["captured_at_utc"],),
                ).fetchone()
                join = False
                if previous and not previous["manual"] and previous["app_name"] == row["app_name"]:
                    at = datetime.fromisoformat(row["captured_at_utc"])
                    gap = (at - datetime.fromisoformat(previous["ended_at_utc"])).total_seconds()
                    span = (at - datetime.fromisoformat(previous["started_at_utc"])).total_seconds()
                    similarity = SequenceMatcher(
                        None,
                        normalize_text(previous["title"])[:512],
                        normalize_text(row["window_title"])[:512],
                        autojunk=False,
                    ).ratio()
                    join = (
                        gap <= self.config.session_gap_seconds
                        and span <= self.config.session_max_seconds
                        and similarity >= self.config.session_title_similarity
                        and self.db.execute(
                            "SELECT count(*) FROM session_items WHERE session_id=?",
                            (previous["id"],),
                        ).fetchone()[0]
                        < 4096
                    )
                if join:
                    self.db.execute(
                        "INSERT INTO session_items VALUES (?,?)", (row["id"], previous["id"])
                    )
                    self.db.execute(
                        "UPDATE sessions SET ended_at_utc=?,representative_id=?,"
                        "revision=revision+1 "
                        "WHERE id=?",
                        (row["captured_at_utc"], row["id"], previous["id"]),
                    )
                else:
                    self._new_session([row])
                added += 1
            self.fault("before_session_commit")
            return {"assigned": added, "batch_full": len(rows) == limit}

    def _session_rows(self, ident):
        return self.db.execute(
            """SELECT o.*,length(r.text)>0 AS has_text FROM observations o
            JOIN session_items i ON i.observation_id=o.id
            LEFT JOIN ocr_results r ON r.observation_id=o.id
            WHERE i.session_id=? AND o.storage_state='ready' ORDER BY o.captured_at_utc,o.id""",
            (ident,),
        ).fetchall()

    def session(self, ident, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("invalid_source_page")
        with self.lock:
            session = self.db.execute("SELECT * FROM sessions WHERE id=?", (ident,)).fetchone()
            if session is None:
                return None
            rows = self._session_rows(ident)
            if not rows or any(not self.image_path(r["image_path"]).is_file() for r in rows):
                if self.db.in_transaction:
                    self.db.execute("DELETE FROM sessions WHERE id=?", (ident,))
                else:
                    with self.db:
                        self.db.execute("DELETE FROM sessions WHERE id=?", (ident,))
                return None
            return dict(session) | {
                "source_count": len(rows),
                "sources": [
                    {k: r[k] for k in ("id", "app_name", "window_title", "captured_at_utc")}
                    | {"has_text": bool(r["has_text"])}
                    for r in rows[offset : offset + limit]
                ],
                "next_offset": offset + limit if offset + limit < len(rows) else None,
            }

    def sessions_page(self, *, before=None, app=None, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_session_page_size")
        progress = self.sync_sessions()
        with self.lock:
            where, params = [], []
            position = decode_cursor(before) if before is not None else None
            if app:
                if not isinstance(app, str) or len(app) > 260:
                    raise ValueError("invalid_app_filter")
                where.append("lower(app_name)=lower(?)")
                params.append(app)
            valid = []
            while len(valid) <= limit:
                conditions, values = list(where), list(params)
                if position:
                    conditions.append("(started_at_utc,id)<(?,?)")
                    values.extend(position)
                rows = self.db.execute(
                    "SELECT * FROM sessions"
                    + (" WHERE " + " AND ".join(conditions) if conditions else "")
                    + " ORDER BY started_at_utc DESC,id DESC LIMIT ?",
                    (*values, limit + 1),
                ).fetchall()
                for row in rows:
                    if (item := self.session(row["id"], limit=1)) is not None:
                        valid.append(item)
                    if len(valid) > limit:
                        break
                if len(rows) < limit + 1:
                    break
                position = (rows[-1]["started_at_utc"], rows[-1]["id"])
            result = valid[:limit]
            return {
                "results": result,
                "next_cursor": encode_cursor(
                    {"captured_at_utc": result[-1]["started_at_utc"], "id": result[-1]["id"]}
                )
                if len(valid) > limit
                else None,
                "grouping": progress,
            }

    def split_session(self, ident, before_id):
        with self.lock, self.db:
            if self.session(ident) is None:
                raise ValueError("session_unavailable")
            rows = self._session_rows(ident)
            positions = [i for i, row in enumerate(rows) if row["id"] == before_id]
            if not positions or positions[0] == 0:
                raise ValueError("split_requires_nonfirst_member")
            position = positions[0]
            self.db.execute("DELETE FROM sessions WHERE id=?", (ident,))
            ids = [
                self._new_session(rows[:position], True),
                self._new_session(rows[position:], True),
            ]
            self.fault("before_session_edit_commit")
            return ids

    def merge_sessions(self, ids):
        if (
            not isinstance(ids, list)
            or not 2 <= len(ids) <= 20
            or any(not isinstance(ident, str) for ident in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("merge_requires_2_to_20_distinct_sessions")
        with self.lock, self.db:
            if any(self.session(ident) is None for ident in ids):
                raise ValueError("session_unavailable")
            rows = [r for ident in ids for r in self._session_rows(ident)]
            if len(rows) > 4096:
                raise ValueError("merge_too_large")
            rows.sort(key=lambda row: (row["captured_at_utc"], row["id"]))
            self.db.executemany("DELETE FROM sessions WHERE id=?", [(ident,) for ident in ids])
            result = self._new_session(rows, True)
            self.fault("before_session_edit_commit")
            return result

    def rebuild_sessions(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE manual=0")
        return self.sync_sessions()
