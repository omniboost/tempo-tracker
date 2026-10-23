"""Tempo REST API v4: worklogs lezen en aanmaken."""

from __future__ import annotations

from dataclasses import dataclass

import requests

TIMEOUT = 20
PAGE_SIZE = 1000


@dataclass(frozen=True)
class TempoWorklog:
    worklog_id: str
    issue_id: str
    start_time: str  # "HH:MM:SS", lokale tijd
    seconds: int
    description: str
    day: str = ""  # "YYYY-MM-DD"


class TempoError(Exception):
    pass


class TempoClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Accept"] = "application/json"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.session.request(method, self.base_url + path, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as error:
            raise TempoError(f"Tempo unreachable: {error.__class__.__name__}") from None
        if response.status_code in (401, 403):
            raise TempoError(f"Tempo rejected the token (HTTP {response.status_code}). Check token and permissions (Worklogs: View + Manage).")
        if not response.ok:
            raise TempoError(f"Tempo returned HTTP {response.status_code}: {_error_messages(response)}")
        return response.json()

    def check(self, account_id: str, day: str) -> None:
        self._request("GET", f"/worklogs/user/{account_id}", params={"from": day, "to": day, "limit": 1})

    def worklogs(self, account_id: str, day: str, until: str | None = None) -> list[TempoWorklog]:
        """Alle worklogs van deze gebruiker van `day` t/m `until` (standaard alleen die dag). Alleen lezen."""
        worklogs, offset = [], 0
        while True:
            data = self._request(
                "GET", f"/worklogs/user/{account_id}",
                params={"from": day, "to": until or day, "offset": offset, "limit": PAGE_SIZE},
            )
            results = data.get("results", [])
            worklogs += [
                TempoWorklog(
                    str(w["tempoWorklogId"]),
                    str(w.get("issue", {}).get("id", "")),
                    w.get("startTime") or "00:00:00",
                    int(w.get("timeSpentSeconds", 0)),
                    w.get("description") or "",
                    w.get("startDate") or day,
                )
                for w in results
            ]
            if len(results) < PAGE_SIZE:
                return worklogs
            offset += PAGE_SIZE

    def create_worklog(
        self, issue_id: str, account_id: str, day: str, start_time: str, seconds: int, description: str
    ) -> str:
        data = self._request(
            "POST",
            "/worklogs",
            json={
                "issueId": int(issue_id),
                "authorAccountId": account_id,
                "startDate": day,
                "startTime": start_time,
                "timeSpentSeconds": seconds,
                "description": description,
            },
        )
        return str(data["tempoWorklogId"])


def _error_messages(response: requests.Response) -> str:
    try:
        errors = response.json().get("errors", [])
        return "; ".join(e.get("message", "") for e in errors) or response.reason
    except ValueError:
        return response.reason
