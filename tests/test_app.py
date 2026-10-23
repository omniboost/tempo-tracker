import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tempo_tracker import app as app_module
from tempo_tracker.config import Config
from tempo_tracker.jira import Issue
from tempo_tracker.tempo import TempoError
from tempo_tracker.tracker import PAUSED

DAY = "2026-10-19"


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 19, hour, minute).timestamp()


class OverviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Config(jira_site="company.atlassian.net", email="me@example.com", fixed_options=[{"label": "Meetings", "issue_key": ""}], git_folders="", shortcut="", week_reminder="")
        patches = [
            mock.patch.object(app_module, "DB_PATH", Path(self.tmp.name) / "t.db"),
            mock.patch.object(app_module, "load_config", return_value=config),
            mock.patch.object(app_module, "save_config"),
            mock.patch.object(app_module.credentials, "get_token", return_value="token"),
            mock.patch.object(app_module, "idle_seconds", return_value=0),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

        self.app = app_module.TempoTrackerApp()
        self.app.timer.stop()
        self.app._last_refresh = self.app._last_week_refresh = float("inf")  # geen verversing op de achtergrond
        self.jira = mock.Mock()
        self.jira.myself.return_value = "acc-1"
        self.jira.get_issue.side_effect = lambda key: Issue(key, {"IN-1": "101", "IN-2": "102", "IN-100": "200", "IN-9": "900"}[key], "x")
        self.jira.issues.side_effect = lambda keys: {key: self.jira.get_issue(key) for key in keys}
        self.tempo = mock.Mock()
        self.tempo.create_worklog.side_effect = lambda *args: f"w-{args[0]}"
        self.tempo.worklogs.return_value = []
        self.app._jira = lambda: self.jira
        self.app._tempo = lambda: self.tempo

        store = self.app.store
        store.add_segment("IN-1", "IN-1 · Kassa koppelen", at(9), at(10, 10))
        store.add_segment("@Meetings", "Meetings", at(10, 10), at(10, 50))
        store.add_segment("IN-2", "IN-2 · Bug", at(11), at(11, 5))

    def run_overview(self, edit, choose=0):
        """`edit(lines)` past de regels aan zoals de gebruiker zou doen; daarna 'Versturen'."""
        calls = []

        def fake_overview(title, text, lines, totals=None, chips=()):
            calls.append([line.label for line in lines])
            if len(calls) > 1:
                return None, lines
            edit(lines)
            return "send", lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview), \
             mock.patch.object(app_module.dialogs, "choose", return_value=choose) as chosen, \
             mock.patch.object(app_module.dialogs, "message") as message:
            self.app._overview(DAY, False)
        return calls, chosen, message

    def test_sends_edited_lines_and_remembers_the_meeting_key(self):
        def edit(lines):
            lines[0].duration = "1:00"
            lines[0].description = "Koppeling getest"
            lines[1].target_key = "IN-100"
            lines[2].duration = "0:00"  # IN-2 schrappen
            lines[3].target_key, lines[3].duration = "IN-9", "0:30"  # handmatige regel

        _, _, message = self.run_overview(edit)

        created = [c.args for c in self.tempo.create_worklog.call_args_list]
        self.assertEqual(
            [
                ("101", "acc-1", DAY, "09:00:00", 3600, "Koppeling getest"),
                ("200", "acc-1", DAY, "10:10:00", 2700, "Meetings"),
                ("900", "acc-1", DAY, "09:00:00", 1800, "IN-9"),
            ],
            created,
        )
        self.assertEqual("Sent to Tempo", message.call_args.args[0])
        self.assertEqual("IN-100", self.app.config.fixed_issue_key("Meetings"))
        self.assertEqual([], self.app.store.unsent_rows(DAY))
        self.assertEqual(3600 + 2700 + 1800, self.app.store.sent_seconds(DAY))

    def test_unmapped_meeting_blocks_sending_and_reopens_the_overview(self):
        calls, _, message = self.run_overview(lambda lines: None)
        self.tempo.create_worklog.assert_not_called()
        self.assertIn("link a Jira key", message.call_args.args[1])
        self.assertEqual(2, len(calls))

    def test_back_from_confirmation_sends_nothing(self):
        def edit(lines):
            lines[1].target_key = "IN-100"

        calls, _, _ = self.run_overview(edit, choose=1)
        self.tempo.create_worklog.assert_not_called()
        self.assertEqual(2, len(calls))

    def test_failed_worklog_stays_open_as_draft(self):
        def edit(lines):
            lines[1].target_key = "IN-100"
            lines[1].duration = "1:00"

        def create(issue_id, *args):
            if issue_id == "200":
                raise TempoError("Period is closed")
            return "w-1"

        self.tempo.create_worklog.side_effect = create
        _, _, message = self.run_overview(edit)
        self.assertEqual("Not everything was sent", message.call_args.args[0])
        self.assertEqual(["@Meetings"], [r.issue_key for r in self.app.store.unsent_rows(DAY)])
        self.assertEqual(3600, self.app.store.drafts(DAY)["@Meetings"]["seconds"])

    def test_invalid_duration_is_reported(self):
        def edit(lines):
            lines[0].duration = "veel"

        calls, _, message = self.run_overview(edit)
        self.assertIn("Invalid duration", message.call_args.args[1])
        self.tempo.create_worklog.assert_not_called()

    def test_saving_a_zeroed_line_removes_it(self):
        lines_seen = []

        def fake_overview(title, text, lines, totals=None, chips=()):
            lines_seen.append([line.issue_key for line in lines if line.issue_key])
            if len(lines_seen) == 1:
                lines[2].duration = "0:00"
                return "save", lines
            return None, lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)
            self.app._overview(DAY, False)

        self.assertEqual(["IN-1", "@Meetings"], lines_seen[1])
        self.assertEqual(["IN-1", "@Meetings"], [r.issue_key for r in self.app.store.unsent_rows(DAY)])
        self.tempo.create_worklog.assert_not_called()

    def test_removed_lines_are_discarded_when_sending(self):
        def edit(lines):
            lines[1].removed = True  # Meetings zonder key: geen fout, want verwijderd
            lines[2].removed = True
            lines[3].removed = True  # lege regel: wordt genegeerd

        _, chosen, _ = self.run_overview(edit)

        self.assertEqual([("101", "acc-1", DAY, "09:00:00", 4500, "Kassa koppelen")], [c.args for c in self.tempo.create_worklog.call_args_list])
        self.assertIn("2 line(s) will be removed", chosen.call_args_list[0].args[1])
        self.assertEqual([], self.app.store.unsent_rows(DAY))

    def test_removing_a_saved_manual_line(self):
        self.app.store.save_draft(DAY, "IN-9", "IN-9", 1800, 0, "")

        def fake_overview(title, text, lines, totals=None, chips=()):
            next(line for line in lines if line.issue_key == "IN-9").removed = True
            return "save", lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)
        self.assertNotIn("IN-9", self.app.store.drafts(DAY))

    def test_meetings_booked_in_tempo_are_subtracted_and_shown(self):
        from tempo_tracker.tempo import TempoWorklog
        self.app.store.record_worklog(DAY, "IN-7", 900, "", "w-own")
        self.tempo.worklogs.return_value = [
            TempoWorklog("w-ext", "300", "09:30:00", 900, "Standup"),
            TempoWorklog("w-own", "700", "08:00:00", 900, ""),
        ]
        self.jira.get_issue.side_effect = lambda key: Issue("IN-123", "300", "Meetings")
        seen = {}

        def fake_overview(title, text, lines, totals=None, chips=()):
            seen["chips"], seen["lines"] = chips, [(line.issue_key, line.tracked) for line in lines if line.issue_key]
            return None, lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)

        self.assertEqual(["09:30–09:45  IN-123  Standup  0:15"], seen["chips"])
        self.assertEqual([("IN-1", "0:55"), ("@Meetings", "0:40"), ("IN-2", "0:05")], seen["lines"])

    def test_overview_still_opens_when_tempo_cannot_be_read(self):
        self.tempo.worklogs.side_effect = TempoError("Tempo unreachable: ConnectionError")
        seen = {}

        def fake_overview(title, text, lines, totals=None, chips=()):
            seen["text"] = text
            return None, lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)
        self.assertIn("Couldn't read your worklogs from Tempo", seen["text"])

    def test_commit_messages_become_the_default_description(self):
        self.app._commit_descriptions = lambda day: {"IN-1": "Add tips; Void a check"}
        placeholders = {}

        def edit(lines):
            placeholders.update({line.issue_key: line.description_placeholder for line in lines})
            lines[1].target_key = "IN-100"

        self.run_overview(edit)
        self.assertEqual("Add tips; Void a check", placeholders["IN-1"])
        self.assertEqual("Add tips; Void a check", self.tempo.create_worklog.call_args_list[0].args[5])

    def test_totals_add_tempo_to_what_will_be_sent(self):
        from tempo_tracker.tempo import TempoWorklog
        self.app.store.record_worklog(DAY, "IN-7", 1800, "", "w-own")
        self.tempo.worklogs.return_value = [
            TempoWorklog("w-ext", "300", "09:30:00", 900, "Standup"),
            TempoWorklog("w-own", "700", "08:00:00", 1800, ""),
        ]
        self.jira.get_issue.side_effect = lambda key: Issue("IN-123", "300", "Meetings")
        texts = []

        def fake_overview(title, text, lines, totals=None, chips=()):
            texts.append(totals([(line.duration, line.removed) for line in lines])[0])
            texts.append(totals([("2:00", False), ("1:00", True), ("veel", False)])[0])
            return None, lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)

        self.assertEqual("In Tempo 0:45 + to send 2:00 = 2:45 of 8:00  ·  5:15 missing", texts[0])
        self.assertEqual("In Tempo 0:45 + to send 2:00 = 2:45 of 8:00  ·  5:15 missing", texts[1])

    def test_totals_over_complete_and_without_target_or_tempo(self):
        self.assertEqual("In Tempo 6:00 + to send 2:30 = 8:30 of 8:00  ·  0:30 over", self.app._totals_text([("2:30", False)], 6 * 3600))
        self.assertEqual("In Tempo 8:00 + to send 0:00 = 8:00 of 8:00  ·  complete ✓", self.app._totals_text([], 8 * 3600))
        self.assertEqual("To send 1:00 (couldn't read Tempo) of 8:00  ·  7:00 missing", self.app._totals_text([("1:00", False)], None))
        self.app.config.day_target = ""
        self.assertEqual("In Tempo 1:00 + to send 1:00 = 2:00", self.app._totals_text([("1:00", False)], 3600))

    def test_edited_time_is_marked_and_resetting_it_removes_the_saved_value(self):
        self.app.store.save_draft(DAY, "IN-1", "IN-1 · Kassa koppelen", 4 * 3600, 70 * 60, "")
        seen = []

        def fake_overview(title, text, lines, totals=None, chips=()):
            seen.append((text, {line.issue_key: (line.duration, line.auto_duration) for line in lines if line.issue_key}))
            next(line for line in lines if line.issue_key == "IN-1").duration = "1:15"  # ✎ geklikt
            return "save", lines

        with mock.patch.object(app_module.dialogs, "overview", side_effect=fake_overview):
            self.app._overview(DAY, False)

        text, durations = seen[0]
        self.assertIn("✎ = a time you changed", text)  # uitleg in de ondertitel
        self.assertEqual(("4:00", "1:15"), durations["IN-1"])
        self.assertEqual(("0:45", ""), durations["@Meetings"])
        self.assertEqual({}, dict(self.app.store.drafts(DAY)))

    def test_app_starts_paused_without_history(self):
        self.assertEqual(PAUSED, self.app.tracker.status)


class TrackingAppCase(unittest.TestCase):
    """App die sinds 9:00 IN-1 bijhoudt; het is 9:45."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(app_module, "DB_PATH", Path(self.tmp.name) / "t.db"),
            mock.patch.object(app_module, "load_config", return_value=Config(jira_site="company.atlassian.net", email="me@example.com", git_folders="", shortcut="", week_reminder="")),
            mock.patch.object(app_module, "save_config"),
            mock.patch.object(app_module.credentials, "get_token", return_value="token"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.app = app_module.TempoTrackerApp()
        self.app.timer.stop()
        self.app._last_refresh = self.app._last_week_refresh = float("inf")  # geen verversing op de achtergrond
        self.app.store.replace_issues("assigned", [app_module.CachedIssue("IN-5", "5", "Hotfix", "assigned")])
        self.now = at(9)
        self.clock = mock.patch.object(app_module.time, "time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.app.tracker.start(app_module.Activity("IN-1", "IN-1 · Kassa"), at(9))
        self.now = at(9, 45)

    def seconds(self, key):
        return next((r.seconds for r in self.app.store.unsent_rows(DAY) if r.issue_key == key), 0)


class CheckinFlowTest(TrackingAppCase):
    def test_yes_plus_something_else_splits_the_period(self):
        splits = []

        def checkin(ticket, period, since, split, **kwargs):
            splits.append(split)
            return 1 if len(splits) == 1 else 0

        hotfix = app_module.PickItem("IN-5", "IN-5 · Hotfix", "assigned")
        with mock.patch.object(app_module.dialogs, "checkin", side_effect=checkin), \
             mock.patch.object(app_module.dialogs, "pick_with_duration", return_value=(hotfix, "15m")):
            self.app._checkin(app_module.CheckinDue(at(9)))

        self.assertEqual([("IN-5 · Hotfix", "0:15"), ("IN-1 · Kassa (rest)", "0:30")], splits[1])
        self.assertEqual((30 * 60, 15 * 60), (self.seconds("IN-1"), self.seconds("IN-5")))

    def test_too_much_extra_time_is_refused_and_asked_again(self):
        hotfix = app_module.PickItem("IN-5", "IN-5 · Hotfix", "toegewezen")
        answers = iter([(hotfix, "1:00"), (hotfix, "0:20")])
        choices = iter([1, 0])
        with mock.patch.object(app_module.dialogs, "checkin", side_effect=lambda *a, **k: next(choices)), \
             mock.patch.object(app_module.dialogs, "pick_with_duration", side_effect=lambda *a, **kw: next(answers)), \
             mock.patch.object(app_module.dialogs, "message") as message:
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual("Too much time", message.call_args.args[0])
        self.assertEqual((25 * 60, 20 * 60), (self.seconds("IN-1"), self.seconds("IN-5")))

    def test_cancelling_the_extra_keeps_everything_on_the_current_ticket(self):
        choices = iter([1, 0])
        with mock.patch.object(app_module.dialogs, "checkin", side_effect=lambda *a, **k: next(choices)), \
             mock.patch.object(app_module.dialogs, "pick_with_duration", return_value=(None, "0:15")):
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual(45 * 60, self.seconds("IN-1"))


class CheckinOtherTicketTest(TrackingAppCase):
    def other_ticket(self, segment):
        hotfix = app_module.PickItem("IN-5", "IN-5 · Hotfix", "assigned")
        with mock.patch.object(app_module.dialogs, "checkin", return_value=2), \
             mock.patch.object(app_module.dialogs, "pick_with_since", return_value=(hotfix, segment, "")) as picker:
            self.app._checkin(app_module.CheckinDue(at(9)))
        return picker

    def test_whole_period_is_the_default(self):
        picker = self.other_ticket(None)
        self.assertEqual("All since 09:00", picker.call_args.args[4][-1])
        self.assertEqual(5, picker.call_args.args[5])
        self.assertEqual((0, 45 * 60), (self.seconds("IN-1"), self.seconds("IN-5")))
        self.assertEqual("IN-5", self.app.tracker.current.key)

    def test_the_last_15_minutes_go_to_the_new_ticket(self):
        self.other_ticket(3)
        self.assertEqual(30 * 60, self.seconds("IN-1"))
        self.assertEqual(("IN-5", at(9, 30)), (self.app.tracker.current.key, self.app.tracker.open_since))
        self.now = at(10)
        self.app.tracker.pause(self.now)
        self.assertEqual(30 * 60, self.seconds("IN-5"))  # 9:30–10:00

    def test_now_keeps_the_period_on_the_current_ticket(self):
        self.other_ticket(0)
        self.assertEqual((45 * 60, 0), (self.seconds("IN-1"), self.seconds("IN-5")))
        self.assertEqual("IN-5", self.app.tracker.current.key)


class CheckinDeferralTest(TrackingAppCase):
    """Later, gesprekken en een check-in waar niemand op antwoordt."""

    def tick_with_checkin_due(self, call=False):
        self.app.tracker.tick = lambda now, idle: app_module.CheckinDue(at(9))
        with mock.patch.object(app_module, "idle_seconds", return_value=0), \
             mock.patch.object(app_module, "in_call", return_value=call), \
             mock.patch.object(self.app, "_checkin") as checkin:
            self.app.on_tick()
        return checkin

    def test_later_asks_again_after_15_minutes(self):
        with mock.patch.object(app_module.dialogs, "checkin", return_value=4):
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual(0, self.seconds("IN-1"))
        self.now = at(9, 59)
        self.tick_with_checkin_due().assert_not_called()
        self.now = at(10, 0)
        self.tick_with_checkin_due().assert_called_once()

    def test_no_check_in_during_a_call_but_right_after(self):
        self.tick_with_checkin_due(call=True).assert_not_called()
        self.assertIn("after your call", self.app.current_item.title)
        self.tick_with_checkin_due(call=False).assert_called_once()

    def test_unanswered_check_in_while_active_counts_as_yes(self):
        with mock.patch.object(app_module.dialogs, "checkin", return_value=app_module.dialogs.TIMED_OUT), \
             mock.patch.object(app_module, "idle_seconds", return_value=5):
            self.now = at(9, 55)
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual(55 * 60, self.seconds("IN-1"))
        self.assertEqual(app_module.TRACKING, self.app.tracker.status)

    def test_unanswered_check_in_while_away_counts_from_the_question_as_away(self):
        def checkin(*args, **kwargs):
            self.assertEqual(10 * 60, kwargs["timeout"])
            self.now = at(9, 55)
            return app_module.dialogs.TIMED_OUT

        with mock.patch.object(app_module.dialogs, "checkin", side_effect=checkin), \
             mock.patch.object(app_module, "idle_seconds", return_value=10 * 60):
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual(45 * 60, self.seconds("IN-1"))
        self.assertEqual(app_module.AWAY, self.app.tracker.status)


class CallTest(TrackingAppCase):
    """Na een gesprek: welke meeting of welk ticket? Vaste opties bovenaan, met een voorstel als je rond deze tijd steeds hetzelfde koos."""

    STANDUP = app_module.PickItem("@Standup", "Standup", "IN-100")

    def setUp(self):
        super().setUp()
        self.app.config.fixed_options = [{"label": "Standup", "issue_key": "IN-100"}, {"label": "Retro", "issue_key": ""}]

    def follow(self, start, end):
        self.now = start
        self.app._follow_call(True, start)
        self.now = end
        self.app._follow_call(False, end)

    def ask(self, *answers):
        with mock.patch.object(app_module.dialogs, "pick", side_effect=list(answers)) as pick:
            self.app._ask_call(*self.app._pending_call)
        return pick

    def test_a_meeting_comes_off_the_ticket(self):
        self.follow(at(9, 30), at(9, 47))
        pick = self.ask((self.STANDUP, 0))
        self.assertIn("09:30–09:47 (0:17)", pick.call_args.args[1])
        self.assertEqual(["@Standup", "@Retro", "IN-1"], [i.key for i in pick.call_args.args[2][:3]])
        self.assertEqual("IN-1", pick.call_args.args[3])  # geen voorstel: het huidige ticket staat geselecteerd
        self.now = at(10)
        self.app.tracker.confirm(self.now)
        self.assertEqual((43 * 60, 17 * 60), (self.seconds("IN-1"), self.seconds("@Standup")))

    def test_a_meeting_without_a_key_asks_once_and_remembers_it(self):
        self.app._jira = lambda: mock.Mock(search=mock.Mock(return_value=[Issue("IN-103", "103", "Retro")]))
        self.follow(at(9, 30), at(9, 47))
        retro = app_module.PickItem("@Retro", "Retro", "fixed")
        pick = self.ask((retro, 0), (app_module.PickItem("IN-103", "IN-103 · Retro", "Jira"), 0))
        self.assertEqual("IN-103", pick.call_args.args[2][0].key)
        self.assertEqual("IN-103", self.app.config.fixed_issue_key("Retro"))
        self.assertEqual(17 * 60, self.seconds("@Retro"))

    def test_unanswered_without_a_suggestion_stays_on_the_ticket(self):
        self.follow(at(9, 30), at(9, 47))
        self.ask((None, app_module.dialogs.TIMED_OUT))
        self.assertEqual([], self.app.tracker.carved)

    def test_keep_on_the_ticket_changes_nothing(self):
        self.follow(at(9, 30), at(9, 47))
        self.ask((None, 1))
        self.assertEqual([], self.app.tracker.carved)

    def test_other_ticket(self):
        self.follow(at(9, 30), at(9, 47))
        self.ask((app_module.PickItem("IN-5", "IN-5 · Hotfix", "assigned"), 0))
        self.assertEqual(17 * 60, self.seconds("IN-5"))

    def test_suggests_the_meeting_you_picked_the_last_two_times(self):
        for day in (15, 16):  # do en vr vorige week, rond 9:30
            start = datetime(2026, 10, day, 9, 32).timestamp()
            self.app.store.add_segment("@Standup", "Standup", start, start + 15 * 60)
        self.follow(at(9, 30), at(9, 47))
        pick = self.ask((None, app_module.dialogs.TIMED_OUT))
        self.assertEqual("@Standup", pick.call_args.args[3])
        self.assertIn("Suggested: you picked Standup the last 2 times around 09:30", pick.call_args.args[1])
        self.assertEqual(17 * 60, self.seconds("@Standup"))  # geen antwoord: het voorstel

    def test_short_calls_are_not_asked(self):
        self.follow(at(9, 30), at(9, 34))
        self.assertIsNone(self.app._pending_call)
        self.app.config.call_min_minutes = 0
        self.follow(at(9, 30), at(10))
        self.assertIsNone(self.app._pending_call)

    def test_check_in_shows_the_call(self):
        self.follow(at(9, 30), at(9, 47))
        self.ask((self.STANDUP, 0))
        self.now = at(10)
        with mock.patch.object(app_module.dialogs, "checkin", return_value=0) as checkin:
            self.app._checkin(app_module.CheckinDue(at(9)))
        self.assertEqual("0:43", checkin.call_args.args[1])
        self.assertEqual([("Standup · call 09:30–09:47", "0:17"), ("IN-1 · Kassa (rest)", "0:43")], checkin.call_args.args[3])

    def test_old_sent_days_are_pruned_once_a_day(self):
        with mock.patch.object(self.app.store, "prune", return_value=0) as prune:
            self.app.store.set_state("pruned", None)
            self.app._prune_daily()
            self.app._prune_daily()
        prune.assert_called_once_with((app_module._today_date() - app_module.timedelta(days=35)).isoformat())

    def tick(self, calling, idle=0):
        with mock.patch.object(app_module, "in_call", return_value=calling), \
             mock.patch.object(app_module, "idle_seconds", return_value=idle), \
             mock.patch.object(self.app, "_ask_call") as ask, \
             mock.patch.object(self.app, "_ask_away") as away, \
             mock.patch.object(self.app, "_checkin") as checkin:
            self.app.on_tick()
        return ask, away, checkin

    def test_a_silent_call_is_not_away_and_the_question_comes_before_the_check_in(self):
        self.app.tracker.touch(at(9, 30))  # de ticks tussendoor
        self.now = at(9, 30)
        self.tick(True)
        self.app.tracker.touch(at(9, 47))
        self.now = at(9, 47)
        ask, away, checkin = self.tick(False, idle=17 * 60)  # 17 min niet getypt tijdens het gesprek
        ask.assert_called_once_with(at(9, 30), at(9, 47))
        away.assert_not_called()
        checkin.assert_not_called()
        self.assertEqual(app_module.TRACKING, self.app.tracker.status)
        self.now = at(9, 48)
        _, _, checkin = self.tick(False, idle=18 * 60)
        checkin.assert_called_once()


class EndOfDayAndAwayTest(TrackingAppCase):
    """Na versturen: klaar voor vandaag, pauze of doorgaan. En 'Welcome back' dat pas veel later beantwoord wordt."""

    def after_sent(self, choice):
        with mock.patch.object(app_module.dialogs, "choose", return_value=choice) as choose:
            self.app._after_sent(app_module.today(), "7 worklogs, 8:00.")
        return choose

    def setUp(self):
        super().setUp()
        self.today = mock.patch.object(app_module, "today", return_value=DAY)
        self.today.start()
        self.addCleanup(self.today.stop)

    def test_done_for_today_stops_and_says_so_in_the_menu(self):
        choose = self.after_sent(app_module.SENT_DONE)
        self.assertEqual(["Done for today", "Pause", "Keep tracking"], choose.call_args.args[2])
        self.assertEqual(PAUSED, self.app.tracker.status)
        self.assertEqual(DAY, self.app.store.get_state("start_prompted"))
        self.app._update_menu()
        self.assertTrue(self.app.current_item.title.startswith("Done for today"))

    def test_pause_and_keep_tracking(self):
        self.after_sent(app_module.SENT_KEEP)
        self.assertEqual(app_module.TRACKING, self.app.tracker.status)
        self.after_sent(app_module.SENT_PAUSE)
        self.assertEqual(PAUSED, self.app.tracker.status)
        self.app._update_menu()
        self.assertTrue(self.app.current_item.title.startswith("Paused"))

    def test_sent_for_another_day_only_reports(self):
        with mock.patch.object(app_module.dialogs, "message") as message:
            self.app._after_sent("2026-10-16", "1 worklog, 1:00.")
        message.assert_called_once()
        self.assertEqual(app_module.TRACKING, self.app.tracker.status)

    def welcome_back(self, answered_at, answers=None):
        self.app.tracker.go_away(at(9, 20))
        answers = list(answers or [(app_module.PAUSE_ITEM, 0)])

        def pick(*args, **kwargs):
            self.now = answered_at
            return answers.pop(0) if answers else (app_module.PAUSE_ITEM, 0)

        with mock.patch.object(app_module.dialogs, "pick", side_effect=pick) as picker:
            self.app._ask_away(app_module.CameBack(at(9, 20), at(9, 45)))
        return picker

    def test_welcome_back_answered_much_later_counts_that_time_as_away_too(self):
        picker = self.welcome_back(at(11))
        self.assertEqual(2, picker.call_count)
        self.assertIn("from 09:45 to 11:00", picker.call_args.args[1])
        self.assertEqual(app_module.TRACKING, self.app.tracker.status)
        self.assertEqual(at(11), self.app.tracker.open_since)

    def test_welcome_back_answered_the_next_day_stops_at_the_question(self):
        picker = self.welcome_back(datetime(2026, 10, 20, 8, 9).timestamp())
        self.assertEqual(1, picker.call_count)
        self.assertEqual(PAUSED, self.app.tracker.status)
        self.assertEqual(20 * 60, self.seconds("IN-1"))  # 9:00–9:20; niets van de nacht

    def test_nothing_happens_while_the_screen_is_off(self):
        with mock.patch.object(app_module, "screen_off", return_value=True), \
             mock.patch.object(app_module, "idle_seconds", return_value=3), \
             mock.patch.object(self.app, "_checkin") as checkin:
            self.app.on_tick()
        checkin.assert_not_called()
        self.assertEqual(app_module.AWAY, self.app.tracker.status)


class RecentMenuTest(TrackingAppCase):
    def test_recent_tickets_are_in_the_menu_and_switch_with_one_click(self):
        self.app.tracker.start(app_module.Activity("IN-5", "IN-5 · Hotfix"), at(9, 30))
        self.app.tracker.start(app_module.Activity.fixed("Meetings"), at(9, 40))
        self.now = at(9, 45)
        self.app._update_menu()
        self.assertEqual(["    IN-5 · Hotfix", "    IN-1 · Kassa"], self.app._recent_titles[::-1])

        self.app.menu["    IN-1 · Kassa"].callback(None)
        self.assertEqual("IN-1", self.app.tracker.current.key)
        self.assertEqual(5 * 60, self.seconds("@Meetings"))
        self.assertIn("    Meetings", self.app._recent_titles)
        self.assertNotIn("    IN-1 · Kassa", self.app._recent_titles)


class WeekFlowTest(TrackingAppCase):
    """Maandag 9:00–10:00 IN-1, dinsdag 9:00–11:00 IN-2 met een standup in Tempo; het is vrijdag 15:30."""

    def setUp(self):
        super().setUp()
        from tempo_tracker.tempo import TempoWorklog
        self.now = at(10)
        self.app.tracker.pause(self.now)
        self.app.store.add_segment("IN-2", "IN-2 · Bug", datetime(2026, 10, 20, 9).timestamp(), datetime(2026, 10, 20, 11).timestamp())
        self.standup = TempoWorklog("w-1", "5", "09:30:00", 1800, "Standup", "2026-10-20")
        self.app._week_tempo = {"2026-10-20": [self.standup]}
        self.now = datetime(2026, 10, 23, 15, 30).timestamp()
        self.app.config.week_reminder = "fri 15:00"

    def test_week_adds_tempo_to_what_is_waiting_and_subtracts_meetings(self):
        days = {d.day: (d.in_tempo, d.to_send) for d in self.app._week_summaries(self.now)}
        self.assertEqual((0, 3600), days["2026-10-19"])
        self.assertEqual((1800, 5400), days["2026-10-20"])  # 2:00 getrackt min de standup van 0:30
        self.assertEqual((0, 0), days["2026-10-21"])

    def test_week_menu_shows_each_workday(self):
        self.app._update_week_menu()
        self.assertEqual("This week  ·  3:00 of 40:00", self.app.week_menu.title)
        self.assertEqual(
            ["Mon 19  1:00  1:00 not sent", "Tue 20  2:00  1:30 not sent", "Wed 21  0:00  nothing tracked",
             "Thu 22  0:00  nothing tracked", "Fri 23  0:00  today"],
            [title for title, _ in self.app._week_entries],
        )

    def reminder(self, choice):
        tempo = mock.Mock()
        tempo.worklogs.return_value = [self.standup]
        self.app._tempo = lambda: tempo
        self.app.store.set_state("account_id", "acc-1")
        with mock.patch.object(app_module.dialogs, "week", return_value=choice) as week, \
             mock.patch.object(self.app, "_overview") as overview:
            self.app._week_reminder()
        tempo.worklogs.assert_called_once_with("acc-1", "2026-10-19", "2026-10-25")
        return week, overview

    def test_reminder_on_friday_afternoon_opens_the_first_day_that_needs_attention(self):
        self.assertTrue(self.app._week_reminder_due())
        week, overview = self.reminder(0)
        args = week.call_args.args
        self.assertEqual("3:00 of 40:00 this week", args[3])
        self.assertEqual(
            ["Monday has 1:00 not sent yet", "Tuesday has 1:30 not sent yet", "Wednesday is empty", "Thursday is empty"],
            args[5],
        )
        self.assertEqual(["Open Monday", "Later"], args[6])
        overview.assert_called_once_with("2026-10-19", False)
        self.assertFalse(self.app._week_reminder_due())  # één keer per week

    def test_later_asks_again_in_an_hour(self):
        _, overview = self.reminder(1)
        overview.assert_not_called()
        self.assertFalse(self.app._week_reminder_due())
        self.now += 3600
        self.assertTrue(self.app._week_reminder_due())

    def test_not_before_the_reminder_moment_and_not_when_off(self):
        self.now = datetime(2026, 10, 23, 14, 59).timestamp()
        self.assertFalse(self.app._week_reminder_due())
        self.now = datetime(2026, 10, 23, 15, 30).timestamp()
        self.app.config.week_reminder = ""
        self.assertFalse(self.app._week_reminder_due())


class StartTimeFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(app_module, "DB_PATH", Path(self.tmp.name) / "t.db"),
            mock.patch.object(app_module, "load_config", return_value=Config(jira_site="company.atlassian.net", email="me@example.com", git_folders="", shortcut="", week_reminder="")),
            mock.patch.object(app_module, "save_config"),
            mock.patch.object(app_module.credentials, "get_token", return_value="token"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.app = app_module.TempoTrackerApp()
        self.app.timer.stop()
        self.app._last_refresh = self.app._last_week_refresh = float("inf")  # geen verversing op de achtergrond
        self.now = at(11, 15)
        clock = mock.patch.object(app_module.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.item = app_module.PickItem("IN-3", "IN-3 · Receipts", "toegewezen")

    def seconds(self, key):
        return next((r.seconds for r in self.app.store.unsent_rows(DAY) if r.issue_key == key), 0)

    def answer(self, *answers):
        """Elk antwoord: een tijd ('9:00') of een knopnummer (0 = Now, 1 = 5m ago, … 4 = 30m)."""
        replies = iter([(self.item, a, "") if isinstance(a, int) else (self.item, None, a) for a in answers])
        return mock.patch.object(app_module.dialogs, "pick_with_since", side_effect=lambda *a, **kw: next(replies))

    def test_start_with_an_earlier_time(self):
        with self.answer("9:00"):
            self.app._ask_start("Waar begin je mee?")
        self.now = at(11, 30)
        self.app.tracker.pause(self.now)
        self.assertEqual(150 * 60, self.seconds("IN-3"))

    def test_now_starts_now(self):
        with self.answer(0):
            self.app._ask_start("Waar begin je mee?")
        self.assertEqual(at(11, 15), self.app.tracker.open_since)

    def test_minutes_ago(self):
        with self.answer(3):
            self.app._ask_start("Waar begin je mee?")
        self.assertEqual(at(11), self.app.tracker.open_since)

    def test_minutes_ago_before_the_open_period_starts_at_the_open_period(self):
        self.app.tracker.start(app_module.Activity("IN-1", "IN-1 · Kassa"), at(11, 5))
        with self.answer(4):
            self.app.on_switch(None)
        self.assertEqual(("IN-3", at(11, 5)), (self.app.tracker.current.key, self.app.tracker.open_since))
        self.assertEqual(0, self.seconds("IN-1"))

    def test_bare_hour_is_accepted(self):
        with self.answer("9"):
            self.app._ask_start("Waar begin je mee?")
        self.assertEqual(at(9), self.app.tracker.open_since)

    def test_overlapping_or_invalid_start_is_refused_and_asked_again(self):
        self.app.tracker.start(app_module.Activity("IN-1", "IN-1 · Kassa"), at(10))
        with self.answer("9:30", "morgen", "12:00", "10:45") as picker, \
             mock.patch.object(app_module.dialogs, "message") as message:
            self.app.on_switch(None)
        self.assertEqual(["Too early", "Invalid time", "Invalid time"], [c.args[0] for c in message.call_args_list])
        self.assertEqual(4, picker.call_count)
        self.assertEqual(45 * 60, self.seconds("IN-1"))
        self.assertEqual(at(10, 45), self.app.tracker.open_since)


class BranchSwitchFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(jira_site="company.atlassian.net", email="me@example.com", git_folders="", shortcut="", week_reminder="")
        patches = [
            mock.patch.object(app_module, "DB_PATH", Path(self.tmp.name) / "t.db"),
            mock.patch.object(app_module, "load_config", return_value=self.config),
            mock.patch.object(app_module, "save_config"),
            mock.patch.object(app_module.credentials, "get_token", return_value="token"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.app = app_module.TempoTrackerApp()
        self.app.timer.stop()
        self.app._last_refresh = self.app._last_week_refresh = float("inf")  # geen verversing op de achtergrond
        self.app.store.replace_issues("assigned", [app_module.CachedIssue("IN-5", "5", "Hotfix", "assigned")])
        self.now = at(10)
        clock = mock.patch.object(app_module.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.app.tracker.start(app_module.Activity("IN-1", "IN-1 · Kassa"), at(9))
        self.switch = app_module.BranchSwitch(Path("/repos/app"), "feature/IN-5-hotfix", "IN-5", at(9, 50))

    def seconds(self, key):
        return next((r.seconds for r in self.app.store.unsent_rows(DAY) if r.issue_key == key), 0)

    def test_ask_moves_the_time_since_the_checkout(self):
        with mock.patch.object(app_module.dialogs, "choose", return_value=0) as choose:
            self.app._on_branch_switch(self.switch)
        self.assertEqual(["Switch to IN-5", "Not now"], choose.call_args.args[2])
        self.assertEqual("IN-5 · Hotfix", self.app.tracker.current.label)
        self.app.tracker.pause(at(10, 10))
        self.assertEqual((50 * 60, 20 * 60), (self.seconds("IN-1"), self.seconds("IN-5")))

    def test_not_now_changes_nothing(self):
        with mock.patch.object(app_module.dialogs, "choose", return_value=1):
            self.app._on_branch_switch(self.switch)
        self.assertEqual("IN-1", self.app.tracker.current.key)

    def test_auto_switches_without_asking(self):
        self.config.branch_switch = "auto"
        with mock.patch.object(app_module.dialogs, "choose") as choose:
            self.app._on_branch_switch(self.switch)
        choose.assert_not_called()
        self.assertEqual("IN-5", self.app.tracker.current.key)

    def test_auto_still_asks_while_paused(self):
        self.config.branch_switch = "auto"
        self.app.tracker.pause(at(9, 30))
        with mock.patch.object(app_module.dialogs, "choose", return_value=0) as choose:
            self.app._on_branch_switch(self.switch)
        self.assertEqual("Start IN-5", choose.call_args.args[2][0])
        self.assertEqual(at(10), self.app.tracker.open_since)  # gepauzeerd: vanaf nu, niet vanaf de checkout

    def test_checkout_before_the_open_period_starts_at_the_open_period(self):
        self.app.tracker.start(app_module.Activity("IN-2", "IN-2 · Bug"), at(9, 55))
        with mock.patch.object(app_module.dialogs, "choose", return_value=0):
            self.app._on_branch_switch(self.switch)
        self.assertEqual(at(9, 55), self.app.tracker.open_since)
        self.assertEqual(0, self.seconds("IN-2"))

    def test_unknown_key_is_looked_up_in_jira(self):
        jira = mock.Mock()
        jira.get_issue.return_value = Issue("IN-77", "77", "New thing")
        self.app._jira = lambda: jira
        switch = app_module.BranchSwitch(Path("/repos/app"), "feature/IN-77", "IN-77", at(9, 50))
        with mock.patch.object(app_module.dialogs, "choose", return_value=0):
            self.app._on_branch_switch(switch)
        self.assertEqual("IN-77 · New thing", self.app.tracker.current.label)

    def test_switch_to_the_current_ticket_asks_nothing(self):
        same = app_module.BranchSwitch(Path("/repos/app"), "IN-1-x", "IN-1", at(9, 50))
        with mock.patch.object(app_module.dialogs, "choose") as choose:
            self.app._on_branch_switch(same)
        choose.assert_not_called()

    def test_off_ignores_switches(self):
        self.config.git_folders = "/repos"
        self.app._last_repo_scan = self.now
        self.app.branches.poll = lambda: self.switch
        self.assertEqual(self.switch, self.app._poll_branches())
        self.config.branch_switch = "off"
        self.assertIsNone(self.app._poll_branches())

    def tick(self, switch=None, event=None, idle=0):
        self.app._poll_branches = lambda: switch
        self.app.tracker.tick = lambda now, idle_: event
        with mock.patch.object(app_module, "idle_seconds", return_value=idle), \
             mock.patch.object(self.app, "_on_branch_switch") as handle, \
             mock.patch.object(self.app, "_checkin"):
            self.app.on_tick()
        return handle

    def test_a_switch_waits_while_another_question_or_absence_is_going_on(self):
        self.tick(self.switch, event=app_module.CheckinDue(at(9))).assert_not_called()
        self.tick(None, idle=5 * 60).assert_not_called()
        self.tick(None).assert_called_once_with(self.switch)
        self.tick(None).assert_not_called()


if __name__ == "__main__":
    unittest.main()
