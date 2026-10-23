"""Dagoverzicht: getrackte tijd per ticket, afgerond en aangepast met opgeslagen concepten."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .config import Config
from .store import Store


@dataclass
class OverviewRow:
    issue_key: str  # lokale sleutel: Jira-key of "@Vaste optie"
    label: str
    tracked_seconds: int
    seconds: int
    description: str
    first_started_at: float | None
    target_key: str  # Jira-key waar de worklog op komt
    auto_seconds: int = 0  # wat het zonder jouw aanpassing zou zijn: getrackt, afgerond

    @property
    def is_fixed(self) -> bool:
        return self.issue_key.startswith("@")

    @property
    def edited(self) -> bool:
        """Getrackte regel waarvan jij de tijd hebt aangepast en opgeslagen."""
        return self.first_started_at is not None and self.seconds != self.auto_seconds


def round_seconds(seconds: int, round_minutes: int) -> int:
    """Afronden op het dichtstbijzijnde blok, maar nooit naar 0 als er wel tijd was."""
    if round_minutes <= 0 or seconds <= 0:
        return seconds
    block = round_minutes * 60
    return max(block, int(round(seconds / block)) * block)


def build_overview(store: Store, config: Config, day: str, booked: list[tuple[float, float]] = ()) -> list[OverviewRow]:
    """`booked`: periodes die al in Tempo staan; die tijd gaat af van de getrackte tijd."""
    drafts = store.drafts(day)
    rows: list[OverviewRow] = []
    for row in store.unsent_rows(day, booked):
        draft = drafts.pop(row.issue_key, None)
        if draft is None:
            seconds, description = round_seconds(row.seconds, config.round_minutes), ""
        else:
            # Sinds het opslaan bijgekomen tijd telt erbij; tijd die inmiddels in Tempo staat gaat eraf.
            change = row.seconds - draft["base_seconds"]
            seconds = draft["seconds"]
            if change:
                seconds = round_seconds(max(0, seconds + change), config.round_minutes)
            description = draft["description"]
        rows.append(
            OverviewRow(
                issue_key=row.issue_key,
                label=row.label,
                tracked_seconds=row.seconds,
                seconds=seconds,
                description=description,
                first_started_at=row.first_started_at,
                target_key=_target_key(row.issue_key, config),
                auto_seconds=round_seconds(row.seconds, config.round_minutes),
            )
        )
    for key, draft in drafts.items():
        rows.append(
            OverviewRow(key, draft["label"], 0, draft["seconds"], draft["description"], None, _target_key(key, config))
        )
    return rows


def _target_key(issue_key: str, config: Config) -> str:
    if issue_key.startswith("@"):
        return config.fixed_issue_key(issue_key[1:])
    return issue_key


def default_description(issue_key: str, label: str) -> str:
    """Tempo-omschrijving als je niets invult: de issuetitel of de naam van de vaste optie."""
    if issue_key.startswith("@"):
        return label
    _, _, summary = label.partition(" · ")
    return summary or label


def format_duration(seconds: int) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"


def parse_duration(value: str) -> int:
    """'1:30', '1h30', '1h', '45m', '1,5' of '1.5' (uren) -> seconden. Leeg = 0."""
    text = value.strip().lower().replace(" ", "")
    if not text:
        return 0
    if match := re.fullmatch(r"(\d+):(\d{1,2})", text):
        hours, minutes = int(match.group(1)), int(match.group(2))
        if minutes > 59:
            raise ValueError(f"Invalid duration: {value!r}")
    elif match := re.fullmatch(r"(\d+(?:[.,]\d+)?)[hu]?", text):
        total_minutes = math.floor(float(match.group(1).replace(",", ".")) * 60 + 0.5)
        hours, minutes = divmod(total_minutes, 60)
    elif match := re.fullmatch(r"(\d+)[hu](\d+)(?:m|min)?", text):
        hours, minutes = int(match.group(1)), int(match.group(2))
    elif match := re.fullmatch(r"(\d+)(?:m|min)", text):
        hours, minutes = 0, int(match.group(1))
    else:
        raise ValueError(f"Invalid duration: {value!r} (use e.g. 1:30, 1h30, 45m or 1.5)")
    seconds = (hours * 60 + minutes) * 60
    if seconds > 24 * 3600:
        raise ValueError(f"More than 24 hours: {value!r}")
    return seconds
