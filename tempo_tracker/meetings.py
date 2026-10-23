"""Welke meeting was dit gesprek? Een voorstel op basis van je eigen eerdere keuzes rond hetzelfde tijdstip."""

from __future__ import annotations

from datetime import datetime, timedelta

WINDOW_SECONDS = 15 * 60  # zo dicht bij hetzelfde tijdstip telt als 'rond dezelfde tijd'
SAME_WEEKDAY_DAYS = 28  # dezelfde weekdag: zo ver terug
ANY_DAY_DAYS = 7  # elke dag: zo ver terug
MEETING_MAX_SECONDS = 2 * 3600  # langere stukken op een vaste optie zijn geen meeting
LOOKBACK_SECONDS = (SAME_WEEKDAY_DAYS + 1) * 24 * 3600


def suggest(earlier: list[tuple[float, str, str]], call_start: float) -> tuple[str, str] | None:
    """`earlier`: (begin, key, label) van eerdere meetings, nieuwste eerst.

    Kijkt naar meetings die binnen 15 minuten van hetzelfde tijdstip begonnen: op dezelfde weekdag in de
    laatste 4 weken, of op elke dag in de afgelopen week. Waren de laatste twee daarvan dezelfde, dan is dat
    het voorstel (key, label); anders geen.
    """
    call = datetime.fromtimestamp(call_start)
    matches = []
    for started_at, key, label in earlier:
        moment = datetime.fromtimestamp(started_at)
        if moment.date() >= call.date():
            continue
        days_ago = (call.date() - moment.date()).days
        same_weekday = moment.weekday() == call.weekday() and days_ago <= SAME_WEEKDAY_DAYS
        if not (same_weekday or days_ago <= ANY_DAY_DAYS):
            continue
        if abs(_clock_seconds(moment) - _clock_seconds(call)) > WINDOW_SECONDS:
            continue
        if matches and matches[-1][0] == moment.date():
            continue  # per dag alleen de nieuwste
        matches.append((moment.date(), key, label))
    if len(matches) >= 2 and matches[0][1] == matches[1][1]:
        return matches[0][1], matches[0][2]
    return None


def _clock_seconds(moment: datetime) -> float:
    return (moment - moment.replace(hour=0, minute=0, second=0, microsecond=0)) / timedelta(seconds=1)
