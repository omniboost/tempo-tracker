"""Weekoverzicht: per dag wat al in Tempo staat en wat nog verstuurd moet worden, en welke dagen aandacht nodig hebben."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TOLERANCE_SECONDS = 15 * 60  # zoveel minder dan je uren per dag telt nog niet als 'te weinig'
WORK_DAYS = frozenset(range(5))  # maandag t/m vrijdag


@dataclass(frozen=True)
class DaySummary:
    day: str  # "YYYY-MM-DD"
    in_tempo: int
    to_send: int

    @property
    def total(self) -> int:
        return self.in_tempo + self.to_send

    @property
    def date(self) -> date:
        return date.fromisoformat(self.day)

    def is_workday(self, work_days=WORK_DAYS) -> bool:
        return self.date.weekday() in work_days


def week_days(today: date) -> list[str]:
    """Maandag t/m zondag van de week waarin `today` valt."""
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=n)).isoformat() for n in range(7)]


def visible(days: list[DaySummary], work_days=WORK_DAYS) -> list[DaySummary]:
    """Werkdagen altijd, andere dagen alleen als er tijd staat."""
    return [d for d in days if d.is_workday(work_days) or d.total]


def attention(days: list[DaySummary], target: int, today: date, work_days=WORK_DAYS) -> list[tuple[str, str]]:
    """Dagen vóór vandaag die aandacht nodig hebben: [(dag, uitleg)], de belangrijkste eerst per dag."""
    notes = []
    for summary in days:
        if summary.date >= today:
            continue
        name = summary.date.strftime("%A")
        if summary.to_send:
            notes.append((summary.day, f"{name} has {_hm(summary.to_send)} not sent yet"))
        elif summary.is_workday(work_days) and summary.total == 0:
            notes.append((summary.day, f"{name} is empty"))
        elif summary.is_workday(work_days) and target and summary.total < target - TOLERANCE_SECONDS:
            notes.append((summary.day, f"{name} has only {_hm(summary.total)}"))
    return notes


def parse_work_days(value: str) -> frozenset[int]:
    """'mon-fri' of 'mon, tue, thu' (ook 'mon-thu, sat') -> {0, 1, 2, 3, 4}. Leeg = geen werkdagen."""
    days: set[int] = set()
    for part in value.lower().replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        try:
            start, end = WEEKDAYS.index(first[:3]), WEEKDAYS.index((last or first)[:3])
        except ValueError:
            raise ValueError(f"Invalid work days {value!r} (use e.g. mon-fri or mon, tue, thu)") from None
        if end < start:
            raise ValueError(f"Invalid work days {value!r}: {part} runs backwards")
        days.update(range(start, end + 1))
    return frozenset(days)


def format_work_days(days) -> str:
    """{0, 1, 2, 3, 4} -> 'mon-fri'; {0, 2, 3} -> 'mon, wed-thu'."""
    parts, ordered = [], sorted(days)
    while ordered:
        start = end = ordered.pop(0)
        while ordered and ordered[0] == end + 1:
            end = ordered.pop(0)
        parts.append(WEEKDAYS[start] if start == end else f"{WEEKDAYS[start]}-{WEEKDAYS[end]}")
    return ", ".join(parts)


def parse_reminder(value: str) -> tuple[int, int, int] | None:
    """'fri 15:00' -> (4, 15, 0): weekdag (0 = maandag), uur, minuut. Leeg = uit (None)."""
    text = value.strip().lower()
    if not text:
        return None
    match = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{1,2})[:.](\d{2})", text)
    if not match or match.group(1) not in WEEKDAYS or int(match.group(2)) > 23 or int(match.group(3)) > 59:
        raise ValueError(f"Invalid reminder {value!r} (use e.g. fri 15:00, or leave empty)")
    return WEEKDAYS.index(match.group(1)), int(match.group(2)), int(match.group(3))


def reminder_moment(reminder: tuple[int, int, int], now: datetime) -> datetime:
    """Het herinneringsmoment in de week van `now`."""
    weekday, hour, minute = reminder
    monday = now.date() - timedelta(days=now.weekday())
    return datetime.combine(monday + timedelta(days=weekday), datetime.min.time()).replace(hour=hour, minute=minute)


def _hm(seconds: int) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"
