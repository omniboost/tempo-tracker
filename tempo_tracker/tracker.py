"""Tijdregistratie als toestandsmachine. Geen UI en geen klok: de tijd wordt meegegeven, zodat dit testbaar is.

Model: er is altijd hooguit één open periode (vanaf `open_since`) voor de huidige activiteit.
Een check-in, wissel, pauze of afwezigheid sluit die periode af en schrijft hem als segment weg.
Een gesprek binnen de open periode wordt er direct uitgesneden (`carved`): die tijd telt niet meer voor het ticket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from .store import Store, day_of

TRACKING = "tracking"
AWAY = "away"
PAUSED = "paused"

# Een tick-gat groter dan dit betekent dat de Mac sliep (timers lopen dan niet).
SLEEP_GAP_SECONDS = 180


def start_of_day(timestamp: float) -> float:
    return datetime.fromtimestamp(timestamp).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@dataclass(frozen=True)
class Activity:
    key: str  # Jira-key ("IN-123") of vaste optie ("@Meetings")
    label: str

    @property
    def is_fixed(self) -> bool:
        return self.key.startswith("@")

    @staticmethod
    def fixed(label: str) -> "Activity":
        return Activity(f"@{label}", label)


@dataclass(frozen=True)
class CheckinDue:
    since: float


@dataclass(frozen=True)
class CameBack:
    away_since: float
    back_at: float


@dataclass(frozen=True)
class DayStart:
    pass


@dataclass(frozen=True)
class EndOfDay:
    day: str


Event = CheckinDue | CameBack | DayStart | EndOfDay

# Losse stukken tijd binnen een periode, bijv. "15 min aan IN-5" tijdens een check-in.
Extras = tuple[tuple[Activity, int], ...]

# Uitgesneden stukken in de open periode: (begin, eind, label), al als segment opgeslagen.
Carved = list[tuple[float, float, str]]


class Tracker:
    def __init__(self, store: Store, checkin_seconds: int, idle_seconds: int, end_of_day_minutes: int):
        self.store = store
        self.checkin_seconds = checkin_seconds
        self.idle_seconds = idle_seconds
        self.end_of_day_minutes = end_of_day_minutes
        self.status = PAUSED
        self.current: Activity | None = None
        self.open_since: float | None = None
        self.last_checkin: float | None = None
        self.away_since: float | None = None
        self.last_tick: float | None = None
        self.carved: Carved = []
        self.work_days: set[int] = {0, 1, 2, 3, 4}  # 0 = maandag; op andere dagen geen 'Good morning'

    # -- persistentie -------------------------------------------------------

    def _save(self) -> None:
        self.store.set_state(
            "tracker",
            json.dumps(
                {
                    "status": self.status,
                    "current": [self.current.key, self.current.label] if self.current else None,
                    "open_since": self.open_since,
                    "last_checkin": self.last_checkin,
                    "away_since": self.away_since,
                    "last_tick": self.last_tick,
                    "carved": self.carved,
                }
            ),
        )

    def restore(self, now: float) -> None:
        """Na een herstart: kort weg -> gewoon doorgaan; lang weg -> als afwezigheid behandelen."""
        raw = self.store.get_state("tracker")
        if not raw:
            return
        data = json.loads(raw)
        self.status = data["status"]
        self.current = Activity(*data["current"]) if data["current"] else None
        self.open_since = data["open_since"]
        self.last_checkin = data["last_checkin"]
        self.away_since = data["away_since"]
        self.last_tick = data["last_tick"]
        self.carved = [tuple(piece) for piece in data.get("carved", [])]
        if self.status == TRACKING and self.last_tick and now - self.last_tick > SLEEP_GAP_SECONDS:
            self.go_away(self.last_tick)
        self.last_tick = now
        self._save()

    # -- acties -------------------------------------------------------------

    def touch(self, at: float) -> None:
        """Er was net interactie (dialoog, actie): geen slaapstand, ook al liep de timer even niet."""
        self.last_tick = max(self.last_tick or at, at)

    def _commit(self, activity: Activity | None, start: float | None, end: float, extras: Extras = ()) -> None:
        """Schrijf [start, end] weg, zonder de uitgesneden gesprekken; `extras` gaan van het eind af, de rest naar `activity`."""
        if start is None:
            return
        pieces = self._free(start, end)
        for extra, seconds in reversed(extras):
            while seconds > 0 and pieces:
                piece_start, piece_end = pieces.pop()
                extra_start = max(piece_start, piece_end - seconds)
                self.store.add_segment(extra.key, extra.label, extra_start, piece_end)
                seconds -= piece_end - extra_start
                if extra_start > piece_start:
                    pieces.append((piece_start, extra_start))
        if activity:
            for piece_start, piece_end in pieces:
                self.store.add_segment(activity.key, activity.label, piece_start, piece_end)
        self.carved = [(max(begin, end), finish, label) for begin, finish, label in self.carved if finish > end]

    def _free(self, start: float, end: float) -> list[tuple[float, float]]:
        """[start, end] min de uitgesneden stukken, op volgorde."""
        pieces, cursor = [], start
        for begin, finish, _ in sorted(self.carved):
            if begin > cursor:
                pieces.append((cursor, min(begin, end)))
            cursor = max(cursor, finish)
            if cursor >= end:
                break
        if cursor < end:
            pieces.append((cursor, end))
        return [(a, b) for a, b in pieces if b > a]

    def carve(self, activity: Activity, start: float, end: float) -> None:
        """Een gesprek (start–end) uit de open periode halen en direct op `activity` zetten; de check-in-timer loopt door."""
        if self.status != TRACKING:
            return
        start = max(start, self.open_since)
        for begin, finish in self._free(start, end):
            self.store.add_segment(activity.key, activity.label, begin, finish)
            self.carved.append((begin, finish, activity.label))
        self.carved.sort()
        self._save()

    def open_seconds(self, at: float) -> int:
        """Lengte van de open periode: de tijd die je nog kunt verdelen."""
        if self.status != TRACKING or self.open_since is None:
            return 0
        return int(sum(end - start for start, end in self._free(self.open_since, at)))

    def start(self, activity: Activity, at: float, since: float | None = None) -> None:
        """Begin (of wissel naar) een activiteit vanaf `since` (standaard nu); de check-in-timer begint nu.

        `since` mag niet vóór de open periode liggen; zie earliest_start().
        """
        since = at if since is None else min(since, at)
        if self.status == TRACKING:
            self._commit(self.current, self.open_since, since)
        self.status = TRACKING
        self.current = activity
        self.open_since = since
        self.last_checkin = at
        self.away_since = None
        self.store.set_state("day_started", day_of(at))
        self.touch(at)
        self._save()

    def earliest_start(self, at: float) -> float:
        """Vroegst toegestane begintijd bij starten of wisselen: niet vóór middernacht en zonder overlap."""
        earliest = max(self.store.last_end(day_of(at)) or 0, start_of_day(at))
        if self.status == TRACKING:
            earliest = max(earliest, self.open_since)
        return earliest

    def confirm(self, at: float, extras: Extras = ()) -> None:
        """Check-in 'ja': de periode sinds de vorige check-in hoort bij de huidige activiteit (min `extras`)."""
        self._commit(self.current, self.open_since, at, extras)
        self.open_since = at
        self.last_checkin = at
        self.touch(at)
        self._save()

    def reassign(self, activity: Activity, at: float, extras: Extras = ()) -> None:
        """Check-in 'ander ticket': de periode sinds de vorige check-in hoort bij `activity`, en die loopt door."""
        self._commit(activity, self.open_since, at, extras)
        self.current = activity
        self.open_since = at
        self.last_checkin = at
        self.touch(at)
        self._save()

    def flush(self, at: float) -> None:
        """Schrijf de open periode weg zonder de check-in-timer te resetten (voor het dagoverzicht)."""
        if self.status == TRACKING:
            self._commit(self.current, self.open_since, at)
            self.open_since = at
            self._save()

    def add_time(self, activity: Activity, seconds: int, at: float) -> None:
        """'Tijd toevoegen' tussendoor: de laatste `seconds` gingen naar `activity`; de check-in-timer loopt door.

        Tijdens een open periode wordt de tijd daaruit gehaald, anders komt hij er los bij (eindigend nu).
        """
        if self.status == TRACKING:
            self._commit(self.current, self.open_since, at, ((activity, seconds),))
            self.open_since = at
        else:
            self._commit(activity, at - seconds, at)
        self.touch(at)
        self._save()

    def pause(self, at: float, extras: Extras = ()) -> None:
        if self.status == TRACKING:
            self._commit(self.current, self.open_since, at, extras)
        self.status = PAUSED
        self.open_since = None
        self.away_since = None
        self.touch(at)
        self._save()

    def go_away(self, since: float) -> None:
        if self.status != TRACKING:
            return
        since = max(since, self.open_since or since)
        self._commit(self.current, self.open_since, since)
        self.status = AWAY
        self.open_since = None
        self.away_since = since
        self._save()

    def resolve_away(self, activity: Activity | None, back_at: float, resume: bool = True) -> None:
        """Afwezigheid toekennen aan `activity` (None = pauze, niet registreren) en daarna verder."""
        if self.status != AWAY:
            return
        self._commit(activity, self.away_since, back_at)
        self.away_since = None
        if resume and self.current:
            self.status = TRACKING
            self.open_since = back_at
            self.last_checkin = back_at
        else:
            self.status = PAUSED
            self.open_since = None
        self.touch(back_at)
        self._save()

    # -- klok ---------------------------------------------------------------

    def tick(self, now: float, idle_seconds: float, present: bool = True) -> Event | None:
        """Elke paar seconden aanroepen met de huidige inactiviteit; geeft hooguit één gebeurtenis terug.

        `present` False (scherm uit of vergrendeld): je bent er niet, ook al was er net nog invoer.
        """
        previous_tick = self.last_tick
        self.last_tick = now
        is_idle = idle_seconds >= self.idle_seconds or not present

        if self.status == TRACKING and previous_tick and now - previous_tick > SLEEP_GAP_SECONDS:
            self.go_away(previous_tick)
        if self.status == TRACKING and is_idle:
            self.go_away(now - idle_seconds)

        event: Event | None = None
        today = day_of(now)
        if self.status == AWAY and not is_idle and day_of(self.away_since) != today:
            # Weg sinds een eerdere dag: niets meer vragen over gisteren, de dag begint opnieuw.
            self.resolve_away(None, now, resume=False)
        if self.status == AWAY and not is_idle:
            event = CameBack(self.away_since, now - idle_seconds)
        elif not is_idle and self._end_of_day_due(now, today):
            self.store.set_state("eod_prompted", today)
            event = EndOfDay(today)
        elif self.status == TRACKING and now - (self.last_checkin or now) >= self.checkin_seconds:
            event = CheckinDue(min(self.last_checkin, self.open_since))
        elif (
            self.status == PAUSED
            and not is_idle
            and datetime.fromtimestamp(now).weekday() in self.work_days
            and self.store.get_state("day_started") != today
            and self.store.get_state("start_prompted") != today
        ):
            self.store.set_state("start_prompted", today)
            event = DayStart()

        self._save()
        return event

    def _end_of_day_due(self, now: float, today: str) -> bool:
        moment = datetime.fromtimestamp(now)
        if moment.hour * 60 + moment.minute < self.end_of_day_minutes:
            return False
        if self.store.get_state("eod_prompted") == today:
            return False
        return self.store.tracked_seconds(today) > 0 or self.status == TRACKING

    def seconds_today(self, now: float) -> int:
        total = self.store.tracked_seconds(day_of(now))
        if self.status == TRACKING and self.open_since:
            total += int(sum(end - start for start, end in self._free(max(self.open_since, _midnight(now)), now)))
        return total


def _midnight(timestamp: float) -> float:
    moment = datetime.fromtimestamp(timestamp)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
