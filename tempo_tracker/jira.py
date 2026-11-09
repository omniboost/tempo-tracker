"""Jira Cloud REST API (alleen lezen). Werkt met scoped tokens (via api.atlassian.com) én classic tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

TIMEOUT = 15
KEY_EXTRA_DIGITS = 2  # "IN-495" also finds IN-4950..IN-49599, not longer keys
KEY_MAX_PROJECTS = 5  # a bare number is tried in at most this many projects


def key_candidates(text: str, projects: list[str]) -> list[str]:
    """Keys that start with what you typed: 'IN-495' or a bare '495' (then in each of `projects`).

    Jira can't search on part of a key and ignores key ranges, so the candidates are listed one by one.
    Returns [] when the text doesn't look like (part of) a key.
    """
    match = re.fullmatch(r"(?:([A-Za-z][A-Za-z0-9_]*)-)?(\d+)", text.strip())
    if not match:
        return []
    project, number = match.groups()
    if project:
        prefixes = [project.upper()]
    else:
        prefixes = [p for p in dict.fromkeys(projects) if re.fullmatch(r"[A-Z][A-Z0-9_]*", p)][:KEY_MAX_PROJECTS]
    numbers = [number]
    for extra in range(1, KEY_EXTRA_DIGITS + 1):
        numbers += [f"{number}{n:0{extra}d}" for n in range(10**extra)]
    return [f"{prefix}-{n}" for prefix in prefixes for n in numbers]


class JiraError(Exception):
    pass


@dataclass
class Issue:
    key: str
    issue_id: str
    summary: str


class JiraClient:
    def __init__(self, site: str, email: str, token: str, session: requests.Session | None = None):
        self.site = site
        self.session = session or requests.Session()
        self.session.auth = (email, token)
        self.session.headers["Accept"] = "application/json"
        self._base: str | None = None
        self.account_id: str | None = None

    def _candidate_bases(self) -> list[str]:
        bases = []
        cloud_id = self._cloud_id()
        if cloud_id:
            bases.append(f"https://api.atlassian.com/ex/jira/{cloud_id}")
        bases.append(f"https://{self.site}")
        return bases

    def _cloud_id(self) -> str | None:
        try:
            response = requests.get(f"https://{self.site}/_edge/tenant_info", timeout=TIMEOUT)
            response.raise_for_status()
            return response.json().get("cloudId")
        except (requests.RequestException, ValueError):
            return None

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Scoped tokens werken alleen via de API-gateway, classic tokens via het eigen domein; probeer beide."""
        bases = [self._base] if self._base else self._candidate_bases()
        last_error = ""
        for base in bases:
            try:
                response = self.session.request(method, base + path, timeout=TIMEOUT, **kwargs)
            except requests.RequestException as error:
                raise JiraError(f"Jira unreachable: {error.__class__.__name__}") from None
            if response.status_code in (401, 403) and not self._base:
                last_error = f"{response.status_code}"
                continue
            if response.status_code == 404:
                raise JiraError(f"Not found in Jira: {path}")
            if not response.ok:
                raise JiraError(f"Jira returned HTTP {response.status_code}")
            self._base = base
            return response.json()
        raise JiraError(
            f"Jira rejected the token (HTTP {last_error}). Check email, token and scopes "
            "(read:jira-work, read:jira-user)."
        )

    def myself(self) -> str:
        if not self.account_id:
            self.account_id = self._request("GET", "/rest/api/3/myself")["accountId"]
        return self.account_id

    def search(self, jql: str, limit: int = 50) -> list[Issue]:
        data = self._request(
            "POST",
            "/rest/api/3/search/jql",
            json={"jql": jql, "fields": ["summary"], "maxResults": limit},
        )
        return [Issue(i["key"], str(i["id"]), i["fields"]["summary"]) for i in data.get("issues", [])]

    def search_text(self, text: str, limit: int = 20) -> list[Issue]:
        """Issues waarvan de titel met elk woord uit `text` begint of dat bevat (voor de ticketkiezer)."""
        words = re.findall(r"\w+", text)  # laat aanhalingstekens en JQL-tekens in de invoer weg
        if not words:
            return []
        conditions = " AND ".join(f'summary ~ "{word}*"' for word in words)
        return self.search(f"{conditions} ORDER BY updated DESC", limit=limit)

    def search_key_prefix(self, text: str, projects: list[str], limit: int = 20) -> list[Issue]:
        """Issues whose key starts with `text` (see key_candidates); unknown keys are skipped by Jira."""
        keys = key_candidates(text, projects)
        if not keys:
            return []
        listed = ", ".join(f'"{key}"' for key in keys)
        return self.search(f"key in ({listed}) ORDER BY key ASC", limit=limit)

    def assigned_issues(self) -> list[Issue]:
        return self.search("assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC")

    def recent_issues(self) -> list[Issue]:
        """Recent bekeken en recent door mij bijgewerkte issues; een JQL-functie die niet werkt wordt overgeslagen."""
        account_id = self.myself()
        queries = [
            "issuekey IN issueHistory() ORDER BY lastViewed DESC",
            f'issuekey IN updatedBy("{account_id}", "-14d") ORDER BY updated DESC',
        ]
        seen: dict[str, Issue] = {}
        for jql in queries:
            try:
                for issue in self.search(jql, limit=30):
                    seen.setdefault(issue.key, issue)
            except JiraError:
                continue
        return list(seen.values())

    def issues(self, keys: list[str]) -> dict[str, Issue]:
        """Meerdere issues in één zoekvraag (key -> Issue). Onbekende keys ontbreken in het resultaat.

        Een onbekende key laat de hele zoekvraag mislukken; dan per key apart.
        """
        keys = sorted(set(keys))
        if not keys:
            return {}
        try:
            return {issue.key: issue for issue in self.search(f"key in ({', '.join(keys)})", limit=len(keys))}
        except JiraError:
            found = {}
            for key in keys:
                try:
                    found[key] = self.get_issue(key)
                except JiraError:
                    continue
            return found

    def get_issue(self, key: str) -> Issue:
        data = self._request("GET", f"/rest/api/3/issue/{key}", params={"fields": "summary"})
        return Issue(data["key"], str(data["id"]), data["fields"]["summary"])
