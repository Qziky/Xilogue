"""Local-calendar review snapshots and explicit feedback; cards retain live source joins."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from xilogue.processing.ocr_types import normalize_text


def day_window(day, timezone):
    if not isinstance(timezone, str) or len(timezone) > 128:
        raise ValueError("invalid_timezone")
    try:
        selected = date.fromisoformat(day)
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError, TypeError) as exc:
        raise ValueError("invalid_review_day") from exc
    start = datetime.combine(selected, time.min, zone).astimezone(UTC)
    end = datetime.combine(selected + timedelta(days=1), time.min, zone).astimezone(UTC)
    return start.isoformat(), end.isoformat()


def migrate_reviews(db):
    for statement in (
        """CREATE TABLE IF NOT EXISTS daily_reviews (
            id TEXT PRIMARY KEY,review_date TEXT NOT NULL,timezone TEXT NOT NULL,
            start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,created_at_utc TEXT NOT NULL,
            UNIQUE(review_date,timezone))""",
        """CREATE TABLE IF NOT EXISTS review_cards (
            review_id TEXT REFERENCES daily_reviews(id) ON DELETE CASCADE,
            session_id TEXT REFERENCES insights(session_id) ON DELETE CASCADE,
            score REAL NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(review_id,session_id))""",
        """CREATE TABLE IF NOT EXISTS review_feedback (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0,
            correction TEXT NOT NULL DEFAULT '',updated_at_utc TEXT NOT NULL)""",
    ):
        db.execute(statement)


class ReviewStorage:
    def muted_topics(self):
        with self.lock:
            result = set()
            for row in self.db.execute(
                "SELECT session_id FROM review_feedback WHERE kind='never'"
            ).fetchall():
                insight = self.insight(row[0])
                if insight:
                    result.update(normalize_text(t) for t in insight["payload"]["topics"])
            return result

    def feedback(self, ident, changes):
        if not isinstance(changes, dict) or set(changes) - {"kind", "hidden", "correction"}:
            raise ValueError("invalid_feedback")
        with self.lock, self.db:
            if self.session(ident) is None:
                raise ValueError("session_unavailable")
            old = self.db.execute(
                "SELECT * FROM review_feedback WHERE session_id=?", (ident,)
            ).fetchone()
            values = (
                {"kind": "neutral", "hidden": False, "correction": ""}
                | (dict(old) if old else {})
                | changes
            )
            if values["kind"] not in ("neutral", "useful", "useless", "later", "mastered", "never"):
                raise ValueError("invalid_feedback_kind")
            if "hidden" in changes and type(changes["hidden"]) is not bool:
                raise ValueError("invalid_hidden_value")
            values["hidden"] = bool(values["hidden"])
            if not isinstance(values["correction"], str) or len(values["correction"]) > 2000:
                raise ValueError("correction_too_long")
            self.db.execute(
                "INSERT OR REPLACE INTO review_feedback VALUES (?,?,?,?,?)",
                (
                    ident,
                    values["kind"],
                    int(values["hidden"]),
                    values["correction"],
                    datetime.now(UTC).isoformat(),
                ),
            )
            return {k: values[k] for k in ("kind", "hidden", "correction")}

    def _day_claims(self, insight, start, end):
        claims = []
        for claim in insight["payload"]["claims"]:
            source = self.observation(claim["source_id"])
            if source and start <= source["captured_at_utc"] < end:
                claims.append(claim)
        return claims

    def generate_review(self, day, timezone):
        start, end = day_window(day, timezone)
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT id FROM daily_reviews WHERE review_date=? AND timezone=?", (day, timezone)
            ).fetchone()
            ident = row[0] if row else uuid4().hex
            self.db.execute(
                "INSERT OR REPLACE INTO daily_reviews VALUES (?,?,?,?,?,?)",
                (ident, day, timezone, start, end, datetime.now(UTC).isoformat()),
            )
            sessions = self.db.execute(
                """SELECT DISTINCT s.id FROM sessions s JOIN insights n ON n.session_id=s.id
                JOIN session_items i ON i.session_id=s.id
                JOIN observations o ON o.id=i.observation_id
                WHERE o.storage_state='ready' AND o.captured_at_utc>=? AND o.captured_at_utc<?
                ORDER BY s.started_at_utc LIMIT 1000""",
                (start, end),
            ).fetchall()
            for session in sessions:
                insight = self.insight(session[0])
                if insight is None or not self._day_claims(insight, start, end):
                    continue
                p = insight["payload"]
                # Explicit model scores, never time-on-screen or inferred learning.
                score = 0.6 * p["importance"] + 0.4 * p["confidence"]
                reason = (
                    f"本地模型重要度 {p['importance']:.2f}，"
                    f"置信度 {p['confidence']:.2f}；有当天原文引用"
                )
                self.db.execute(
                    "INSERT INTO review_cards VALUES (?,?,?,?)", (ident, session[0], score, reason)
                )
            self.fault("before_review_commit")
            return self.review(day, timezone)

    def review(self, day, timezone, *, include_hidden=False, limit=30, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("invalid_review_page")
        start, end = day_window(day, timezone)
        with self.lock:
            review = self.db.execute(
                "SELECT * FROM daily_reviews WHERE review_date=? AND timezone=?", (day, timezone)
            ).fetchone()
            if review is None:
                return {"review_date": day, "timezone": timezone, "generated": False, "cards": []}
            muted = self.muted_topics()
            rows = self.db.execute(
                """SELECT c.*,f.kind,f.hidden,f.correction FROM review_cards c
                LEFT JOIN review_feedback f ON f.session_id=c.session_id
                WHERE c.review_id=? ORDER BY c.score DESC,c.session_id""",
                (review["id"],),
            ).fetchall()
            cards = []
            for row in rows:
                insight = self.insight(row["session_id"])
                if insight is None:
                    continue
                claims = self._day_claims(insight, start, end)
                if not claims:
                    continue
                topics = insight["payload"]["topics"]
                suppressed = (
                    row["hidden"]
                    or row["kind"] in ("useless", "mastered", "never")
                    or bool(muted & {normalize_text(t) for t in topics})
                )
                if suppressed and not include_hidden:
                    continue
                cards.append(
                    {
                        "session_id": row["session_id"],
                        "summary": "；".join(c["text"] for c in claims),
                        "claims": claims,
                        "topics": topics,
                        "reason": row["reason"],
                        "score": row["score"],
                        "feedback": row["kind"] or "neutral",
                        "hidden": bool(suppressed),
                        "correction": row["correction"] or "",
                    }
                )
            total = len(cards)
            return dict(review) | {
                "generated": True,
                "cards": cards[offset : offset + limit],
                "total_visible": total,
                "next_offset": offset + limit if offset + limit < total else None,
                "empty_reason": "没有可用的当天摘要，或已被反馈隐藏。" if not cards else "",
            }
