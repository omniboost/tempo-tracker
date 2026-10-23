"""Instellingen: JSON in ~/Library/Application Support/TempoTracker (tokens staan in de Keychain)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "TempoTracker"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "tracker.db"
LOG_PATH = Path.home() / "Library" / "Logs" / "TempoTracker.log"

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def default_fixed_options() -> list[dict]:
    return [
        {"label": "Meetings", "issue_key": ""},
        {"label": "Support", "issue_key": ""},
        {"label": "Other", "issue_key": ""},
    ]


@dataclass
class Config:
    jira_site: str = ""  # bv. company.atlassian.net; leeg = bij de eerste start vragen
    email: str = ""
    tempo_base_url: str = "https://api.tempo.io/4"
    checkin_minutes: int = 45
    idle_minutes: int = 10
    end_of_day: str = "18:00"
    round_minutes: int = 15
    fixed_options: list[dict] = field(default_factory=default_fixed_options)
    git_folders: str = "~/Projects"  # komma-gescheiden; leeg = geen git
    branch_switch: str = "ask"  # ask | auto | off
    shortcut: str = "ctrl+alt+t"  # leeg = uit
    day_target: str = "8:00"  # uren per dag voor 'nog te gaan'; leeg = niet tonen
    week_reminder: str = "fri 15:00"  # weekseintje; leeg = uit
    work_days: str = "mon-fri"  # op andere dagen vraagt de app niets uit zichzelf
    call_min_minutes: int = 5  # na een gesprek van minstens zo lang: vragen wat het was; 0 = nooit

    @property
    def git_roots(self) -> list[str]:
        return [folder.strip() for folder in self.git_folders.split(",") if folder.strip()]

    @property
    def is_complete(self) -> bool:
        return bool(self.jira_site and self.email)

    def fixed_issue_key(self, label: str) -> str:
        for option in self.fixed_options:
            if option["label"] == label:
                return option.get("issue_key", "")
        return ""

    def end_of_day_minutes(self) -> int:
        hours, minutes = parse_clock(self.end_of_day)
        return hours * 60 + minutes


def normalize_site(value: str) -> str:
    """'company', 'https://company.atlassian.net/jira' -> 'company.atlassian.net'."""
    site = value.strip().lower()
    site = re.sub(r"^https?://", "", site).split("/")[0]
    if site and "." not in site:
        site = f"{site}.atlassian.net"
    return site


def parse_clock(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value)
    if not match:
        raise ValueError(f"Invalid time: {value!r} (use hh:mm)")
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        raise ValueError(f"Invalid time: {value!r}")
    return hours, minutes


def parse_fixed_options(value: str) -> list[dict]:
    """'Meetings=IN-100, Support, Overig=IN-7' -> [{'label': 'Meetings', 'issue_key': 'IN-100'}, ...]."""
    options = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        label, _, key = part.partition("=")
        label, key = label.strip(), key.strip().upper()
        if key and not ISSUE_KEY_RE.match(key):
            raise ValueError(f"Invalid Jira key for {label!r}: {key!r}")
        options.append({"label": label, "issue_key": key})
    return options


def format_fixed_options(options: list[dict]) -> str:
    return ", ".join(
        f"{o['label']}={o['issue_key']}" if o.get("issue_key") else o["label"] for o in options
    )


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    data = json.loads(path.read_text())
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
