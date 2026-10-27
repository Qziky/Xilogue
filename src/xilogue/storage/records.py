"""Bounded source browsing with stable cursors, including unprocessed images."""

import base64
import binascii
import json
from datetime import UTC, datetime

from xilogue.processing.ocr_types import normalize_text


def filters(app, after, before):
    where, values, bounds = [], [], []
    if app:
        if not isinstance(app, str) or len(app) > 260:
            raise ValueError("invalid_app_filter")
        where.append("lower(o.app_name)=lower(?)")
        values.append(app)
    for bound, operator in ((after, ">="), (before, "<")):
        if bound is None:
            bounds.append(None)
            continue
        at = datetime.fromisoformat(bound)
        if at.tzinfo is None:
            raise ValueError("time_filter_requires_timezone")
        at = at.astimezone(UTC).isoformat()
        bounds.append(at)
        where.append(f"o.captured_at_utc{operator}?")
        values.append(at)
    if all(bounds) and bounds[0] >= bounds[1]:
        raise ValueError("time_range_must_increase")
    return where, values


def encode_cursor(row):
    raw = json.dumps([row["captured_at_utc"], row["id"]]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor):
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
            raise ValueError
        data = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(data, list)
            or len(data) != 2
            or not all(isinstance(item, str) for item in data)
            or len(data[1]) != 32
            or any(char not in "0123456789abcdef" for char in data[1])
        ):
            raise ValueError
        at = datetime.fromisoformat(data[0])
        if at.tzinfo is None:
            raise ValueError
        return at.astimezone(UTC).isoformat(), data[1]
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise ValueError("invalid_cursor") from exc


class RecordStorage:
    def observations_page(
        self, *, query="", limit=30, app=None, after=None, before=None, status=None, cursor=None
    ):
        if not isinstance(query, str) or len(query) > 512:
            raise ValueError("query_must_contain_0_to_512_characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("page_size_must_be_1_to_100")
        if status not in (None, "pending", "running", "done", "failed"):
            raise ValueError("invalid_ocr_status")
        where, values = filters(app, after, before)
        where.append("o.storage_state='ready'")
        if status is not None:
            where.append("j.status=?")
            values.append(status)
        query = normalize_text(query.strip())
        if query:
            where.append("instr(r.normalized,?)>0")
            values.append(query)
            if len(query) >= 3:
                where.append("o.id IN (SELECT observation_id FROM ocr_fts WHERE ocr_fts MATCH ?)")
                values.append('"' + query.replace('"', '""') + '"')
        position = decode_cursor(cursor) if cursor is not None else None
        found = []
        with self.lock:
            # Fetch bounded batches and continue past absent files without an OFFSET
            # that shifts when another source is deleted or a new capture is saved.
            while len(found) <= limit:
                conditions, params = list(where), list(values)
                if position:
                    conditions.append("(o.captured_at_utc,o.id)<(?,?)")
                    params.extend(position)
                rows = self.db.execute(
                    """SELECT o.id,o.app_name,o.window_title,o.captured_at_utc,o.image_path,
                       j.status AS ocr_status,j.attempts AS ocr_attempts,
                       substr(coalesce(r.text,''),
                           max(1,instr(coalesce(r.normalized,''),?)-60),180) AS preview
                       FROM observations o LEFT JOIN processing_jobs j ON j.observation_id=o.id
                       LEFT JOIN ocr_results r ON r.observation_id=o.id WHERE """
                    + " AND ".join(conditions)
                    + " ORDER BY o.captured_at_utc DESC,o.id DESC LIMIT ?",
                    (query, *params, limit + 1),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    if self.image_path(row["image_path"]).is_file():
                        found.append(dict(row))
                    if len(found) > limit:
                        break
                if len(rows) < limit + 1:
                    break
                position = (rows[-1]["captured_at_utc"], rows[-1]["id"])
        page = found[:limit]
        return {
            "results": [{k: v for k, v in row.items() if k != "image_path"} for row in page],
            "next_cursor": encode_cursor(page[-1]) if len(found) > limit else None,
        }
