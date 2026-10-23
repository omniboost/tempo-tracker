import unittest
from datetime import datetime

from tempo_tracker.config import Config, normalize_site, parse_clock, parse_fixed_options
from tempo_tracker.store import Store
from tempo_tracker.summary import build_overview, default_description, format_duration, parse_duration, round_seconds

DAY = "2026-10-20"


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 20, hour, minute).timestamp()


class DurationTest(unittest.TestCase):
    def test_parses_common_notations(self):
        cases = {
            "1:30": 5400, "0:45": 2700, "1h30": 5400, "1u30m": 5400, "2h": 7200,
            "45m": 2700, "45min": 2700, "1,5": 5400, "1.25": 4500, "2": 7200, "": 0, " 0:00 ": 0,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(seconds, parse_duration(text))

    def test_rejects_nonsense(self):
        for text in ("abc", "1:75", "25", "1h30x", "h", "-1"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_formats_as_hours_and_minutes(self):
        self.assertEqual("1:05", format_duration(3900))
        self.assertEqual("0:00", format_duration(0))
        self.assertEqual("0:01", format_duration(59))

    def test_rounds_to_nearest_block_but_never_to_zero(self):
        self.assertEqual(900, round_seconds(5 * 60, 15))
        self.assertEqual(900, round_seconds(22 * 60, 15))
        self.assertEqual(1800, round_seconds(23 * 60, 15))
        self.assertEqual(0, round_seconds(0, 15))
        self.assertEqual(301, round_seconds(301, 0))


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.config = Config(round_minutes=15, fixed_options=[{"label": "Meetings", "issue_key": "IN-100"}, {"label": "Overig", "issue_key": ""}])

    def test_groups_per_ticket_rounded_in_order_of_first_start(self):
        self.store.add_segment("@Meetings", "Meetings", at(9), at(9, 20))
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9, 20), at(10, 0))
        self.store.add_segment("@Meetings", "Meetings", at(10), at(10, 10))
        rows = build_overview(self.store, self.config, DAY)
        self.assertEqual(["@Meetings", "IN-1"], [r.issue_key for r in rows])
        self.assertEqual([30 * 60, 45 * 60], [r.seconds for r in rows])
        self.assertEqual([30 * 60, 40 * 60], [r.tracked_seconds for r in rows])
        self.assertEqual(["IN-100", "IN-1"], [r.target_key for r in rows])

    def test_unmapped_fixed_option_has_no_target(self):
        self.store.add_segment("@Overig", "Overig", at(9), at(10))
        self.assertEqual("", build_overview(self.store, self.config, DAY)[0].target_key)

    def test_draft_overrides_and_later_tracked_time_is_added(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(10))
        self.store.save_draft(DAY, "IN-1", "IN-1 · Kassa", 30 * 60, 60 * 60, "Review")
        row = build_overview(self.store, self.config, DAY)[0]
        self.assertEqual((30 * 60, "Review"), (row.seconds, row.description))

        self.store.add_segment("IN-1", "IN-1 · Kassa", at(10), at(10, 30))
        self.assertEqual(60 * 60, build_overview(self.store, self.config, DAY)[0].seconds)

    def test_manual_draft_without_tracked_time_is_shown(self):
        self.store.save_draft(DAY, "IN-9", "IN-9", 3600, 0, "")
        rows = build_overview(self.store, self.config, DAY)
        self.assertEqual([("IN-9", 3600, 0)], [(r.issue_key, r.seconds, r.tracked_seconds) for r in rows])

    def test_sent_and_discarded_time_disappears_from_the_overview(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(10))
        self.store.add_segment("IN-2", "IN-2 · Bug", at(10), at(11))
        self.store.record_worklog(DAY, "IN-1", 3600, "Kassa", "123")
        self.store.discard_unsent(DAY, "IN-2")
        self.assertEqual([], build_overview(self.store, self.config, DAY))
        self.assertEqual(3600, self.store.sent_seconds(DAY))
        self.assertEqual([], self.store.days_with_unsent())

    def test_default_description(self):
        self.assertEqual("Kassa koppelen", default_description("IN-1", "IN-1 · Kassa koppelen"))
        self.assertEqual("Meetings", default_description("@Meetings", "Meetings"))
        self.assertEqual("IN-9", default_description("IN-9", "IN-9"))


    def test_time_already_in_tempo_is_subtracted(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(10))
        self.store.add_segment("IN-2", "IN-2 · Bug", at(10), at(10, 30))
        booked = [(at(9, 30), at(9, 45)), (at(9, 40), at(10, 10))]  # overlappen elkaar ook
        rows = build_overview(self.store, self.config, DAY, booked)
        self.assertEqual([("IN-1", 30 * 60, 30 * 60), ("IN-2", 20 * 60, 15 * 60)], [(r.issue_key, r.tracked_seconds, r.seconds) for r in rows])

    def test_meeting_logged_in_tempo_after_saving_lowers_the_saved_time(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(11))
        self.store.save_draft(DAY, "IN-1", "IN-1 · Kassa", 150 * 60, 120 * 60, "")
        row = build_overview(self.store, self.config, DAY, [(at(9, 30), at(9, 45))])[0]
        self.assertEqual((105 * 60, 135 * 60), (row.tracked_seconds, row.seconds))

    def test_a_saved_time_that_differs_from_the_tracked_time_is_marked_as_edited(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(10, 27))
        self.store.add_segment("IN-2", "IN-2 · Bug", at(11), at(11, 30))
        self.store.save_draft(DAY, "IN-1", "IN-1 · Kassa", 4 * 3600, 87 * 60, "")
        self.store.save_draft(DAY, "IN-2", "IN-2 · Bug", 30 * 60, 30 * 60, "Fixed it")
        self.store.save_draft(DAY, "IN-9", "IN-9", 3600, 0, "")
        rows = {r.issue_key: r for r in build_overview(self.store, self.config, DAY)}
        self.assertEqual((True, 90 * 60), (rows["IN-1"].edited, rows["IN-1"].auto_seconds))
        self.assertFalse(rows["IN-2"].edited)
        self.assertFalse(rows["IN-9"].edited)  # handmatige regel: geen getrackte tijd om naar terug te gaan

    def test_ticket_fully_covered_by_tempo_shows_zero(self):
        self.store.add_segment("IN-1", "IN-1 · Kassa", at(9, 30), at(9, 45))
        row = build_overview(self.store, self.config, DAY, [(at(9, 30), at(9, 45))])[0]
        self.assertEqual((0, 0), (row.tracked_seconds, row.seconds))

class ConfigTest(unittest.TestCase):
    def test_normalizes_site(self):
        for value in ("company", "https://company.atlassian.net/jira/your-work", " Company.atlassian.net "):
            self.assertEqual("company.atlassian.net", normalize_site(value))

    def test_a_new_config_asks_for_the_site(self):
        self.assertFalse(Config(email="me@example.com").is_complete)
        self.assertTrue(Config(jira_site="company.atlassian.net", email="me@example.com").is_complete)

    def test_parses_fixed_options(self):
        self.assertEqual(
            [{"label": "Meetings", "issue_key": "IN-100"}, {"label": "Overig", "issue_key": ""}],
            parse_fixed_options("Meetings=in-100, Overig, "),
        )
        with self.assertRaises(ValueError):
            parse_fixed_options("Meetings=geen-key")

    def test_parses_clock(self):
        self.assertEqual((18, 0), parse_clock("18:00"))
        self.assertEqual((9, 5), parse_clock("9.05"))
        for value in ("24:00", "18", "6pm"):
            with self.assertRaises(ValueError):
                parse_clock(value)


if __name__ == "__main__":
    unittest.main()
