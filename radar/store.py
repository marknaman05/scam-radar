"""Every message we have seen, with the model's verdict and any human ruling.

SQLite through the standard library; one table.  A human ruling never
overwrites the model's answer -- both are kept, so the review queue can show
what the model thought and the stats can measure how often it was right.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL,             -- sha1 of the normalised text, for "seen N times"
    text         TEXT NOT NULL,
    sender       TEXT,
    received     TEXT NOT NULL,
    -- the model's verdict
    is_scam      INTEGER NOT NULL,
    p_scam       REAL NOT NULL,
    kind         TEXT NOT NULL,
    p_kind       REAL NOT NULL,
    danger       INTEGER NOT NULL,
    creativity   INTEGER NOT NULL,
    needs_review INTEGER NOT NULL,
    review_reason TEXT NOT NULL DEFAULT '',
    verdict_json TEXT NOT NULL,
    -- the human's, if any
    human_is_scam INTEGER,
    human_kind    TEXT,
    reviewed      TEXT,
    -- leaderboard votes
    votes        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reports_fp ON reports (fingerprint);
CREATE INDEX IF NOT EXISTS reports_review ON reports (needs_review, reviewed);
"""


def fingerprint(text: str) -> str:
    # Digits and links vary between copies of the same campaign; collapse them.
    import re
    norm = re.sub(r"\d+", "#", text.lower())
    norm = re.sub(r"https?://\S+|www\.\S+", "<link>", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


class Store:
    #: Columns added after the first release.  The full verdict is always in
    #: verdict_json; these exist so the leaderboard and stats can sort and
    #: group without parsing every row.
    LATER = {
        "content": "TEXT NOT NULL DEFAULT 'scam'",
        "is_misleading": "INTEGER NOT NULL DEFAULT 0",
        "claim_kind": "TEXT NOT NULL DEFAULT 'no_claim'",
        "harm": "INTEGER NOT NULL DEFAULT 0",
        "virality": "INTEGER NOT NULL DEFAULT 0",
        "human_claim_kind": "TEXT",
    }

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
            have = {row["name"] for row in db.execute("PRAGMA table_info(reports)")}
            for column, decl in self.LATER.items():
                if column not in have:
                    db.execute(f"ALTER TABLE reports ADD COLUMN {column} {decl}")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def add(self, text: str, sender: str | None, verdict: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fp = fingerprint(text)
        with self._db() as db:
            cur = db.execute(
                """INSERT INTO reports (fingerprint, text, sender, received, is_scam, p_scam, kind, p_kind, danger,
                                        creativity, needs_review, review_reason, verdict_json,
                                        content, is_misleading, claim_kind, harm, virality)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fp, text, sender, now, int(verdict["is_scam"]), verdict["p_scam"], verdict["kind"], verdict["p_kind"],
                 verdict["danger"], verdict["creativity"], int(verdict["needs_review"]), verdict["review_reason"],
                 json.dumps(verdict),
                 verdict.get("content", "scam"), int(verdict.get("is_misleading", False)),
                 verdict.get("claim_kind", "no_claim"), verdict.get("harm", 0), verdict.get("virality", 0)),
            )
            row = db.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone()
            seen = db.execute("SELECT COUNT(*) FROM reports WHERE fingerprint = ?", (fp,)).fetchone()[0]
        out = self._public(row)
        out["seen_before"] = seen - 1
        return out

    def get(self, report_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return self._public(row) if row else None

    def review_queue(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM reports WHERE needs_review = 1 AND reviewed IS NULL ORDER BY received DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._public(r) for r in rows]

    def rule(self, report_id: int, is_scam: bool, kind: str, claim_kind: str | None = None) -> dict | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db() as db:
            db.execute("UPDATE reports SET human_is_scam = ?, human_kind = ?, human_claim_kind = ?, reviewed = ? WHERE id = ?",
                       (int(is_scam), kind, claim_kind, now, report_id))
        return self.get(report_id)

    def vote(self, report_id: int) -> dict | None:
        with self._db() as db:
            db.execute("UPDATE reports SET votes = votes + 1 WHERE id = ?", (report_id,))
        return self.get(report_id)

    def leaderboard(self, limit: int = 20) -> list[dict]:
        """Most creative scams: the model's creativity score, then votes.
        Only confirmed scams (by a human, or confidently by the model)."""
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM reports
                   WHERE COALESCE(human_is_scam, CASE WHEN needs_review = 0 THEN is_scam END) = 1
                   ORDER BY creativity DESC, votes DESC, received DESC LIMIT ?""", (limit,)
            ).fetchall()
        return [self._public(r) for r in rows]

    def recent(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM reports ORDER BY received DESC LIMIT ?", (limit,)).fetchall()
        return [self._public(r) for r in rows]

    def stats(self) -> dict:
        with self._db() as db:
            total, scams, review, reviewed = db.execute(
                """SELECT COUNT(*),
                          SUM(COALESCE(human_is_scam, is_scam)),
                          SUM(CASE WHEN needs_review = 1 AND reviewed IS NULL THEN 1 ELSE 0 END),
                          SUM(CASE WHEN reviewed IS NOT NULL THEN 1 ELSE 0 END)
                   FROM reports"""
            ).fetchone()
            agreed = db.execute(
                "SELECT SUM(CASE WHEN human_is_scam = is_scam AND human_kind = kind THEN 1 ELSE 0 END) FROM reports WHERE reviewed IS NOT NULL"
            ).fetchone()[0]
            kinds = db.execute(
                "SELECT COALESCE(human_kind, kind) AS k, COUNT(*) AS n FROM reports WHERE COALESCE(human_is_scam, is_scam) = 1 GROUP BY k ORDER BY n DESC"
            ).fetchall()
        with self._db() as db:
            misleading = db.execute("SELECT COUNT(*) FROM reports WHERE is_misleading = 1").fetchone()[0]
            claim_kinds = db.execute(
                "SELECT COALESCE(human_claim_kind, claim_kind) AS k, COUNT(*) AS n FROM reports "
                "WHERE is_misleading = 1 GROUP BY k ORDER BY n DESC"
            ).fetchall()
        return {
            "total": total or 0, "scams": scams or 0, "misleading": misleading or 0,
            "awaiting_review": review or 0, "reviewed": reviewed or 0,
            "model_agreed_with_human": agreed or 0, "by_kind": {r["k"]: r["n"] for r in kinds},
            "by_claim": {r["k"]: r["n"] for r in claim_kinds},
        }

    @staticmethod
    def _public(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["verdict"] = json.loads(d.pop("verdict_json"))
        for key in ("is_scam", "needs_review"):
            d[key] = bool(d[key])
        if d["human_is_scam"] is not None:
            d["human_is_scam"] = bool(d["human_is_scam"])
        d["is_misleading"] = bool(d.get("is_misleading"))
        d["final_is_scam"] = d["human_is_scam"] if d["human_is_scam"] is not None else d["is_scam"]
        d["final_kind"] = d["human_kind"] or d["kind"]
        d["final_claim_kind"] = d.get("human_claim_kind") or d.get("claim_kind") or "no_claim"
        # What the card should lead with, once a human has had their say.
        d["headline"] = ("scam, and it lies to sell the bait" if d["final_is_scam"] and d["is_misleading"]
                         else "scam" if d["final_is_scam"]
                         else "false or misleading claim" if d["is_misleading"]
                         else "looks genuine")
        return d
