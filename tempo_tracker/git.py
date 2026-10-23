"""Git: ticket herkennen aan je branch en omschrijvingen maken uit je commits. Alles lokaal, alleen lezen."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Alleen hoofdletters, zodat 'hotfix-123' of 'release-2' geen ticket wordt.
KEY_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9_]+-\d+)(?![0-9])")
# 'feat(IN-42): ', 'IN-42: ', '[IN-42] ' aan het begin van een commitbericht.
COMMIT_PREFIX = re.compile(r"^\s*(?:\w+(?:\([^)]*\))?!?:\s*|\[[^\]]*\]\s*|[A-Z][A-Z0-9_]+-\d+:?\s+)")
SKIP_DIRS = {"node_modules", "vendor", ".venv", "venv", "Library", "build", "dist"}
MAX_DESCRIPTION = 255
GIT_TIMEOUT = 10


def issue_key(branch: str) -> str | None:
    match = KEY_IN_TEXT.search(branch)
    return match.group(1) if match else None


def find_repos(roots: list[str], max_depth: int = 4) -> list[Path]:
    """Git-repo's onder `roots` (niet in repo's zelf zoeken, dus geen submodules of worktrees daarbinnen)."""
    repos = []
    for root in roots:
        base = Path(os.path.expanduser(root.strip()))
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for current, dirs, files in os.walk(base):
            if ".git" in dirs or ".git" in files:
                repos.append(Path(current))
                dirs[:] = []
                continue
            if len(Path(current).parts) - base_depth >= max_depth:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
    return sorted(set(repos))


def _git_dir(repo: Path) -> Path | None:
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    try:
        text = dot_git.read_text().strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        path = Path(text[len("gitdir:"):].strip())
        return path if path.is_absolute() else (repo / path).resolve()
    return None


def current_branch(repo: Path) -> tuple[str | None, float]:
    """(branch, moment van de laatste checkout). Branch is None bij een detached HEAD (bijv. tijdens een rebase)."""
    git_dir = _git_dir(repo)
    if git_dir is None:
        return None, 0.0
    head = git_dir / "HEAD"
    try:
        text = head.read_text().strip()
        changed_at = head.stat().st_mtime
    except OSError:
        return None, 0.0
    if text.startswith("ref: refs/heads/"):
        return text[len("ref: refs/heads/"):], changed_at
    return None, changed_at


@dataclass(frozen=True)
class BranchSwitch:
    repo: Path
    branch: str
    key: str
    at: float


class BranchWatcher:
    """Onthoudt per repo de branch; poll() meldt een wissel naar een branch met een ticketkey."""

    def __init__(self, saved: dict[str, str | None] | None = None) -> None:
        """`saved`: branches van de vorige keer (zie `snapshot`), zodat een wissel tijdens een herstart niet verloren gaat."""
        self.branches: dict[Path, str | None] = {}
        self._saved = saved or {}

    def set_repos(self, repos: list[Path]) -> None:
        """Bekende repo's houden hun branch; nieuwe krijgen de bewaarde branch, of anders stil de huidige."""
        self.branches = {
            repo: self.branches[repo] if repo in self.branches
            else self._saved[str(repo)] if str(repo) in self._saved
            else current_branch(repo)[0]
            for repo in repos
        }

    def snapshot(self) -> dict[str, str | None]:
        return {str(repo): branch for repo, branch in self.branches.items()}

    def poll(self) -> BranchSwitch | None:
        latest = None
        for repo, known in self.branches.items():
            branch, at = current_branch(repo)
            if branch is None or branch == known:
                continue  # detached HEAD (rebase) of niets veranderd: vorige branch blijft gelden
            self.branches[repo] = branch
            key = issue_key(branch)
            if key and (latest is None or at > latest.at):
                latest = BranchSwitch(repo, branch, key, at)
        return latest


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("git in %s failed: %s", repo, error)
        return ""
    return result.stdout if result.returncode == 0 else ""


def _touched_since(repo: Path, timestamp: float) -> bool:
    """Snelle voorselectie: is er sinds `timestamp` iets in de repo gebeurd (reflog van HEAD bijgewerkt)?"""
    git_dir = _git_dir(repo)
    if git_dir is None:
        return False
    try:
        return (git_dir / "logs" / "HEAD").stat().st_mtime >= timestamp
    except OSError:
        return True  # geen reflog: voor de zekerheid meenemen


class CommitLog:
    """Commitberichten van jezelf op een dag, per ticketkey."""

    def __init__(self) -> None:
        self._emails: dict[Path, str] = {}

    def _email(self, repo: Path) -> str:
        if repo not in self._emails:
            self._emails[repo] = _git(repo, "config", "user.email").strip()
        return self._emails[repo]

    def messages_by_key(self, repos: list[Path], day: str) -> dict[str, list[str]]:
        start = datetime.strptime(day, "%Y-%m-%d")
        end = start + timedelta(days=1)
        by_key: dict[str, list[str]] = {}
        for repo in repos:
            if not _touched_since(repo, start.timestamp()):
                continue
            email = self._email(repo)
            if not email:
                continue
            # --since filtert op commitdatum (snel); daarna op auteursdatum, zodat een rebase van vandaag
            # geen oude commits naar vandaag haalt.
            output = _git(
                repo, "log", "--all", "--no-merges", "--reverse", "--format=%at %s",
                f"--author={email}", f"--since={start.isoformat()}",
            )
            for line in output.splitlines():
                authored, _, subject = line.partition(" ")
                if not authored.isdigit() or not start.timestamp() <= int(authored) < end.timestamp():
                    continue
                for key in dict.fromkeys(KEY_IN_TEXT.findall(subject)):
                    if subject not in by_key.setdefault(key, []):
                        by_key[key].append(subject)
        return by_key


def describe(subjects: list[str]) -> str:
    """'feat(IN-42): add tips' + ... -> 'Add tips; …' (max 255 tekens)."""
    parts = []
    for subject in subjects:
        text = COMMIT_PREFIX.sub("", subject, count=1).strip()
        text = text[:1].upper() + text[1:]
        if text and text not in parts:
            parts.append(text)
    description = "; ".join(parts)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip("; ") + "…"
    return description
