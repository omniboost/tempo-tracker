"""Lokale opslag in SQLite. Niets hiervan gaat naar Tempo tot je het dagoverzicht verstuurt."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS worklogs (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    description TEXT NOT NULL,
    tempo_worklog_id TEXT NOT NULL,
    sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    label TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    worklog_id INTEGER  -- NULL = nog niet verstuurd, -1 = verworpen
);
CREATE INDEX IF NOT EXISTS segments_day ON segments(day);
CREATE TABLE IF NOT EXISTS drafts (
    day TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    label TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    base_seconds INTEGER NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (day, issue_key)
);
CREATE TABLE IF NOT EXISTS issues (
    key TEXT NOT NULL,
    source TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (key, source)
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def day_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).date().isoformat()


def next_midnight(timestamp: float) -> float:
    current = datetime.fromtimestamp(timestamp).date() + timedelta(days=1)
    return datetime.combine(current, datetime.min.time()).timestamp()


def _merge(periods) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(periods):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _overlap(start: float, end: float, periods: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in periods)


@dataclass
class DayRow:
    issue_key: str
    label: str
    seconds: int
    first_started_at: float | None


@dataclass
class CachedIssue:
    key: str
    issue_id: str
    summary: str
    source: str


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if str(path) != ":memory:":
            os.chmod(path, 0o600)

    def add_segment(self, issue_key: str, label: str, started_at: float, ended_at: float) -> None:
        """Slaat een periode op, gesplitst op middernacht zodat elke dag klopt."""
        with self.db:
            while ended_at - started_at >= 1:
                split = min(ended_at, next_midnight(started_at))
                self.db.execute(
                    "INSERT INTO segments (day, issue_key, label, started_at, ended_at) VALUES (?, ?, ?, ?, ?)",
                    (day_of(started_at), issue_key, label, started_at, split),
                )
                started_at = split

    def unsent_rows(self, day: str, booked: list[tuple[float, float]] = ()) -> list[DayRow]:
        """Niet-verstuurde tijd per ticket, min de overlap met `booked` (periodes die al in Tempo staan)."""
        segments = self.db.execute(
            "SELECT issue_key, label, started_at, ended_at FROM segments WHERE day = ? AND worklog_id IS NULL ORDER BY started_at",
            (day,),
        ).fetchall()
        booked = _merge(booked)
        rows: dict[str, list] = {}
        for segment in segments:
            seconds = segment["ended_at"] - segment["started_at"] - _overlap(segment["started_at"], segment["ended_at"], booked)
            row = rows.setdefault(segment["issue_key"], [segment["label"], 0.0, segment["started_at"]])
            row[0] = max(row[0], segment["label"])
            row[1] += seconds
        return [DayRow(key, label, int(round(seconds)), first) for key, (label, seconds, first) in rows.items()]

    def own_worklog_ids(self) -> set[str]:
        """Tempo-id's van worklogs die deze app zelf heeft verstuurd."""
        return {r[0] for r in self.db.execute("SELECT tempo_worklog_id FROM worklogs")}

    def sent_seconds(self, day: str) -> int:
        row = self.db.execute("SELECT COALESCE(SUM(seconds), 0) FROM worklogs WHERE day = ?", (day,)).fetchone()
        return int(row[0])

    def tracked_seconds(self, day: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(ended_at - started_at), 0) FROM segments WHERE day = ?", (day,)
        ).fetchone()
        return int(row[0])

    def last_end(self, day: str) -> float | None:
        """Einde van de laatste (niet geschrapte) periode op deze dag."""
        row = self.db.execute(
            "SELECT MAX(ended_at) FROM segments WHERE day = ? AND (worklog_id IS NULL OR worklog_id != -1)", (day,)
        ).fetchone()
        return row[0]

    def days_with_unsent(self, before: str | None = None) -> list[str]:
        query = "SELECT DISTINCT day FROM segments WHERE worklog_id IS NULL"
        params: tuple = ()
        if before:
            query += " AND day < ?"
            params = (before,)
        return [r[0] for r in self.db.execute(query + " ORDER BY day", params)]

    def record_worklog(self, day: str, issue_key: str, seconds: int, description: str, tempo_worklog_id: str) -> None:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO worklogs (day, issue_key, seconds, description, tempo_worklog_id, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                (day, issue_key, seconds, description, tempo_worklog_id, time.time()),
            )
            self.db.execute(
                "UPDATE segments SET worklog_id = ? WHERE day = ? AND issue_key = ? AND worklog_id IS NULL",
                (cursor.lastrowid, day, issue_key),
            )
            self.db.execute("DELETE FROM drafts WHERE day = ? AND issue_key = ?", (day, issue_key))

    def discard_unsent(self, day: str, issue_key: str) -> None:
        """Rij bewust op 0 gezet: tijd telt niet meer mee, maar blijft als 'verworpen' herkenbaar."""
        with self.db:
            self.db.execute(
                "UPDATE segments SET worklog_id = -1 WHERE day = ? AND issue_key = ? AND worklog_id IS NULL",
                (day, issue_key),
            )
            self.db.execute("DELETE FROM drafts WHERE day = ? AND issue_key = ?", (day, issue_key))

    def drafts(self, day: str) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT * FROM drafts WHERE day = ?", (day,)).fetchall()
        return {r["issue_key"]: r for r in rows}

    def save_draft(self, day: str, issue_key: str, label: str, seconds: int, base_seconds: int, description: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO drafts (day, issue_key, label, seconds, base_seconds, description) VALUES (?, ?, ?, ?, ?, ?)",
                (day, issue_key, label, seconds, base_seconds, description),
            )

    def delete_draft(self, day: str, issue_key: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM drafts WHERE day = ? AND issue_key = ?", (day, issue_key))

    def recent_activities(self, limit: int = 15) -> list[tuple[str, str]]:
        rows = self.db.execute(
            "SELECT issue_key, MAX(label) AS label, MAX(ended_at) AS last FROM segments GROUP BY issue_key ORDER BY last DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r["issue_key"], r["label"]) for r in rows]

    def replace_issues(self, source: str, issues: list[CachedIssue]) -> None:
        with self.db:
            self.db.execute("DELETE FROM issues WHERE source = ?", (source,))
            self.db.executemany(
                "INSERT OR REPLACE INTO issues (key, source, issue_id, summary, position) VALUES (?, ?, ?, ?, ?)",
                [(i.key, source, i.issue_id, i.summary, n) for n, i in enumerate(issues)],
            )

    def cached_issues(self, source: str) -> list[CachedIssue]:
        rows = self.db.execute(
            "SELECT * FROM issues WHERE source = ? ORDER BY position", (source,)
        ).fetchall()
        return [CachedIssue(r["key"], r["issue_id"], r["summary"], r["source"]) for r in rows]

    def fixed_segments(self, since: float, max_seconds: int) -> list[tuple[float, str, str]]:
        """(begin, key, label) van korte stukken op een vaste optie (zoals een meeting) vanaf `since`, nieuwste eerst."""
        rows = self.db.execute(
            "SELECT started_at, issue_key, label FROM segments WHERE issue_key LIKE '@%' AND started_at >= ? "
            "AND ended_at - started_at <= ? ORDER BY started_at DESC",
            (since, max_seconds),
        ).fetchall()
        return [(r["started_at"], r["issue_key"], r["label"]) for r in rows]

    def prune(self, before: str) -> int:
        """Wist dagen vóór `before` die helemaal verstuurd zijn (ze staan in Tempo). Geeft het aantal gewiste dagen."""
        keep = "SELECT day FROM segments WHERE worklog_id IS NULL UNION SELECT day FROM drafts"
        days = {r[0] for r in self.db.execute(
            f"SELECT day FROM segments WHERE day < ? AND day NOT IN ({keep}) "
            f"UNION SELECT day FROM worklogs WHERE day < ? AND day NOT IN ({keep})",
            (before, before),
        )}
        if not days:
            return 0
        with self.db:
            for table in ("segments", "worklogs"):
                self.db.executemany(f"DELETE FROM {table} WHERE day = ?", [(day,) for day in days])
        self.db.execute("VACUUM")
        return len(days)

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str | None) -> None:
        with self.db:
            if value is None:
                self.db.execute("DELETE FROM state WHERE key = ?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))


def today() -> str:
    return date.today().isoformat()
