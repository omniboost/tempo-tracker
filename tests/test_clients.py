import unittest
from unittest import mock

import requests

from tempo_tracker.jira import JiraClient, JiraError
from tempo_tracker.tempo import TempoClient, TempoError


def response(status: int, payload: dict | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = (__import__("json").dumps(payload or {})).encode()
    result.reason = "test"
    return result


class JiraClientTest(unittest.TestCase):
    def client(self, *responses):
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.request.side_effect = list(responses)
        client = JiraClient("company.atlassian.net", "me@example.com", "token", session=session)
        return client, session

    @mock.patch("tempo_tracker.jira.requests.get")
    def test_scoped_token_goes_through_the_api_gateway(self, get):
        get.return_value = response(200, {"cloudId": "abc"})
        client, session = self.client(response(200, {"accountId": "acc-1"}))
        self.assertEqual("acc-1", client.myself())
        self.assertEqual("https://api.atlassian.com/ex/jira/abc/rest/api/3/myself", session.request.call_args.args[1])

    @mock.patch("tempo_tracker.jira.requests.get")
    def test_falls_back_to_the_site_for_classic_tokens_and_remembers_it(self, get):
        get.return_value = response(200, {"cloudId": "abc"})
        issue = {"key": "IN-1", "id": "10001", "fields": {"summary": "Kassa"}}
        client, session = self.client(response(401), response(200, issue), response(200, issue))
        self.assertEqual("10001", client.get_issue("IN-1").issue_id)
        client.get_issue("IN-1")
        self.assertEqual("https://company.atlassian.net/rest/api/3/issue/IN-1", session.request.call_args.args[1])
        self.assertEqual(3, session.request.call_count)

    @mock.patch("tempo_tracker.jira.requests.get")
    def test_rejected_token_gives_a_clear_error(self, get):
        get.return_value = response(200, {"cloudId": "abc"})
        client, _ = self.client(response(401), response(401))
        with self.assertRaisesRegex(JiraError, "rejected the token"):
            client.myself()

    @mock.patch("tempo_tracker.jira.requests.get")
    def test_recent_issues_skips_a_failing_query(self, get):
        get.return_value = response(200, {"cloudId": "abc"})
        found = {"issues": [{"key": "IN-2", "id": "2", "fields": {"summary": "Bug"}}]}
        client, _ = self.client(response(200, {"accountId": "acc"}), response(400), response(200, found))
        self.assertEqual(["IN-2"], [i.key for i in client.recent_issues()])

    @mock.patch("tempo_tracker.jira.requests.get")
    def test_text_search_matches_every_word_and_ignores_jql_characters(self, get):
        get.return_value = response(200, {"cloudId": "abc"})
        found = {"issues": [{"key": "IN-3", "id": "3", "fields": {"summary": "Kassa koppeling"}}]}
        client, session = self.client(response(200, found))
        self.assertEqual(["IN-3"], [i.key for i in client.search_text('kassa "kop" \\ OR 1=1')])
        self.assertEqual(
            'summary ~ "kassa*" AND summary ~ "kop*" AND summary ~ "OR*" AND summary ~ "1*" AND summary ~ "1*" ORDER BY updated DESC',
            session.request.call_args.kwargs["json"]["jql"],
        )

    def test_text_search_without_words_does_not_call_jira(self):
        client, session = self.client()
        self.assertEqual([], client.search_text('"" \\'))
        session.request.assert_not_called()


class TempoClientTest(unittest.TestCase):
    def test_creates_a_worklog(self):
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.request.return_value = response(200, {"tempoWorklogId": 42})
        client = TempoClient("https://api.tempo.io/4/", "token", session=session)
        self.assertEqual("42", client.create_worklog("10001", "acc", "2026-10-20", "09:00:00", 5400, "Kassa"))
        method, url = session.request.call_args.args
        self.assertEqual(("POST", "https://api.tempo.io/4/worklogs"), (method, url))
        self.assertEqual(
            {"issueId": 10001, "authorAccountId": "acc", "startDate": "2026-10-20", "startTime": "09:00:00", "timeSpentSeconds": 5400, "description": "Kassa"},
            session.request.call_args.kwargs["json"],
        )
        self.assertEqual("Bearer token", session.headers["Authorization"])

    def test_reads_worklogs_of_a_day_across_pages(self):
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        first = [{"tempoWorklogId": n, "issue": {"id": 7}, "startTime": "09:30:00", "timeSpentSeconds": 900} for n in range(2)]
        second = [{"tempoWorklogId": 9, "issue": {"id": 8}, "timeSpentSeconds": 600, "description": "Standup"}]
        session.request.side_effect = [response(200, {"results": first}), response(200, {"results": second})]
        client = TempoClient("https://api.tempo.io/4", "token", session=session)

        with mock.patch("tempo_tracker.tempo.PAGE_SIZE", 2):
            worklogs = client.worklogs("acc-1", "2026-10-20")

        self.assertEqual(["0", "1", "9"], [w.worklog_id for w in worklogs])
        self.assertEqual(("8", "00:00:00", 600, "Standup"), (worklogs[2].issue_id, worklogs[2].start_time, worklogs[2].seconds, worklogs[2].description))
        method, url = session.request.call_args_list[1].args
        self.assertEqual(("GET", "https://api.tempo.io/4/worklogs/user/acc-1"), (method, url))
        self.assertEqual({"from": "2026-10-20", "to": "2026-10-20", "offset": 2, "limit": 2}, session.request.call_args_list[1].kwargs["params"])

    def test_reports_tempo_validation_errors(self):
        session = mock.Mock(spec=requests.Session)
        session.headers = {}
        session.request.return_value = response(400, {"errors": [{"message": "Period is closed"}]})
        client = TempoClient("https://api.tempo.io/4", "token", session=session)
        with self.assertRaisesRegex(TempoError, "Period is closed"):
            client.create_worklog("1", "acc", "2026-10-20", "09:00:00", 60, "x")


if __name__ == "__main__":
    unittest.main()
