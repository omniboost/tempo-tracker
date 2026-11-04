"""The day as a timeline for the Calendar view: what you tracked, what is already in Tempo, and the gaps between.

Calculation only (no windows), so it can be tested without a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

TRACKED, TEMPO = "tracked", "tempo"
MERGE_GAP = 60  # consecutive pieces of the same ticket (less than a minute apart) become one block
GAP_MIN_SECONDS = 10 * 60  # shorter stretches without time are no gap (a coffee break)
DAY_START_HOUR = 9  # start time on an empty day (also what the app sends to Tempo when no time is known)
SPLIT_MIN_SECONDS = 5 * 60  # sending per period: shorter pieces count towards the nearest period


@dataclass(frozen=True)
class Block:
    start: float
    end: float
    key: str  # Jira key, or "@Standup" for a fixed option
    title: str
    lane: str  # TRACKED or TEMPO: still to send or already in Tempo
    pending: bool = False  # just added in the day overview, not saved yet
    jira_key: str = ""  # the Jira key of a fixed option ('@Standup' -> 'IN-50'); empty for a ticket, whose key is `key`

    @property
    def link_key(self) -> str:
        """The Jira key this block opens."""
        return self.jira_key or self.key


def merge(blocks: list[Block]) -> list[Block]:
    """Joins consecutive blocks of the same ticket in the same lane."""
    merged: list[Block] = []
    for block in sorted(blocks, key=lambda b: ((TRACKED, TEMPO).index(b.lane), b.start)):
        last = merged[-1] if merged else None
        if last and last.lane == block.lane and last.key == block.key and block.start - last.end < MERGE_GAP:
            merged[-1] = replace(last, end=max(last.end, block.end))
        else:
            merged.append(block)
    return merged


def gaps(blocks: list[Block], min_seconds: int = GAP_MIN_SECONDS) -> list[tuple[float, float]]:
    """Periods without tracked time and without Tempo, between the first and the last block of the day."""
    found, covered_until = [], None
    for block in sorted(blocks, key=lambda b: b.start):
        if covered_until is not None and block.start - covered_until >= min_seconds:
            found.append((covered_until, block.start))
        covered_until = block.end if covered_until is None else max(covered_until, block.end)
    return found


def entries(blocks: list[Block]) -> list[tuple[float, float, Block | None]]:
    """Everything of the day in order of start time: (start, end, block), with block None for a gap."""
    items = [(b.start, b.end, b) for b in blocks] + [(start, end, None) for start, end in gaps(blocks)]
    return sorted(items, key=lambda item: (item[0], item[1]))


def free_start(day: str, blocks: list[Block], seconds: int) -> float:
    """Default start time for time you add: the first gap it fits in, otherwise after the last block.

    On an empty day: DAY_START_HOUR.
    """
    fitting = gaps(blocks, min_seconds=max(seconds, 1))
    if fitting:
        return fitting[0][0]
    if blocks:
        return max(b.end for b in blocks)
    return datetime.strptime(day, "%Y-%m-%d").timestamp() + DAY_START_HOUR * 3600


def overlaps(blocks: list[Block], start: float, end: float) -> list[Block]:
    """Blocks that fall (partly) in the period from `start` to `end`."""
    return [b for b in sorted(blocks, key=lambda b: b.start) if b.start < end and start < b.end]


def split(periods: list[tuple[float, float]], booked: list[tuple[float, float]], seconds: int) -> list[tuple[float, int]]:
    """The tracked periods of one ticket as separate worklogs: (start, seconds), together exactly `seconds`.

    Adjoining pieces become one period; what is already in Tempo (`booked`, e.g. a meeting) is cut out;
    periods shorter than SPLIT_MIN_SECONDS count towards the nearest one. The difference with `seconds` (a
    time you changed in the list, or rounding) goes on the last period; less comes off the last one first.
    Every period is a whole number of minutes, except the last, which makes the total add up. Empty if
    there are no periods.
    """
    pieces = []
    for start, end in _joined(periods):
        pieces += [(a, b - a) for a, b in _without(start, end, booked)]
    if not pieces or seconds <= 0:
        return []
    pieces = _fold_short(pieces)
    lengths = [length for _, length in pieces]
    difference = seconds - sum(lengths)
    if difference >= 0:
        lengths[-1] += difference
    else:
        to_remove = -difference
        for n in reversed(range(len(lengths))):
            cut = min(to_remove, lengths[n])
            lengths[n] -= cut
            to_remove -= cut
    kept = [(start, length) for (start, _), length in zip(pieces, lengths) if length > 0]
    rounded = [(start, int(round(length / 60)) * 60) for start, length in kept]
    rounded = [(start, length) for start, length in rounded if length > 0] or [(kept[0][0], 0)]
    last_start, last_length = rounded[-1]
    rounded[-1] = (last_start, last_length + seconds - sum(length for _, length in rounded))
    while len(rounded) > 1 and rounded[-1][1] <= 0:  # the last one is used up: the shortfall goes to the one before
        _, short = rounded.pop()
        start, length = rounded[-1]
        rounded[-1] = (start, length + short)
    return rounded


def _joined(periods: list[tuple[float, float]]) -> list[tuple[float, float]]:
    joined: list[list[float]] = []
    for start, end in sorted(periods):
        if joined and start - joined[-1][1] < MERGE_GAP:
            joined[-1][1] = max(joined[-1][1], end)
        else:
            joined.append([start, end])
    return [(start, end) for start, end in joined]


def _without(start: float, end: float, booked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The period from `start` to `end`, minus the pieces that fall in `booked`."""
    pieces = [(start, end)]
    for a, b in booked:
        if b > a:
            pieces = [part for s, e in pieces for part in ((s, min(e, a)), (max(s, b), e)) if part[1] > part[0]]
    return pieces


def _fold_short(pieces: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """(start, length): the shortest piece under SPLIT_MIN_SECONDS merges into its nearest neighbour."""
    pieces = list(pieces)
    while len(pieces) > 1:
        n = min(range(len(pieces)), key=lambda i: pieces[i][1])
        start, length = pieces[n]
        if length >= SPLIT_MIN_SECONDS:
            break
        neighbours = [i for i in (n - 1, n + 1) if 0 <= i < len(pieces)]
        other = min(neighbours, key=lambda i: abs(pieces[i][0] - start))
        merged = (min(start, pieces[other][0]), length + pieces[other][1])
        low, high = sorted((n, other))
        pieces[low:high + 1] = [merged]
    return pieces
