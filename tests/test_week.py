import unittest
from datetime import date, datetime

from tempo_tracker.week import DaySummary, attention, format_work_days, parse_reminder, parse_work_days, reminder_moment, visible, week_days

H = 3600


class WeekTest(unittest.TestCase):
    def test_week_runs_monday_to_sunday(self):
        self.assertEqual(
            ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"],
            week_days(date(2026, 10, 21)),
        )

    def test_weekend_only_shows_with_time(self):
        days = [DaySummary("2026-10-23", 0, 0), DaySummary("2026-10-24", 0, 0), DaySummary("2026-10-25", 0, H)]
        self.assertEqual(["2026-10-23", "2026-10-25"], [d.day for d in visible(days)])

    def test_attention_for_past_days(self):
        days = [
            DaySummary("2026-10-19", 8 * H, 0),  # compleet
            DaySummary("2026-10-20", 5 * H, 2.5 * H),  # nog te versturen
            DaySummary("2026-10-21", 0, 0),  # leeg
            DaySummary("2026-10-22", 5 * H, 0),  # te weinig
            DaySummary("2026-10-23", 0, 0),  # vandaag: telt nog niet
            DaySummary("2026-10-24", 0, 0),  # weekend leeg: prima
        ]
        self.assertEqual(
            [
                ("2026-10-20", "Tuesday has 2:30 not sent yet"),
                ("2026-10-21", "Wednesday is empty"),
                ("2026-10-22", "Thursday has only 5:00"),
            ],
            attention(days, 8 * H, date(2026, 10, 23)),
        )

    def test_a_little_short_is_fine(self):
        self.assertEqual([], attention([DaySummary("2026-10-19", 7.75 * H, 0)], 8 * H, date(2026, 10, 20)))

    def test_without_target_only_unsent_and_empty_days_count(self):
        days = [DaySummary("2026-10-19", 2 * H, 0), DaySummary("2026-10-20", 0, 0)]
        self.assertEqual([("2026-10-20", "Tuesday is empty")], attention(days, 0, date(2026, 10, 21)))

    def test_reminder(self):
        self.assertEqual((4, 15, 0), parse_reminder("fri 15:00"))
        self.assertEqual((0, 9, 30), parse_reminder("Monday 9.30"))
        self.assertIsNone(parse_reminder(""))
        for wrong in ("friday", "xyz 15:00", "fri 25:00"):
            with self.assertRaises(ValueError):
                parse_reminder(wrong)
        self.assertEqual(datetime(2026, 10, 23, 15, 0), reminder_moment((4, 15, 0), datetime(2026, 10, 20, 11, 0)))


    def test_work_days(self):
        self.assertEqual(frozenset(range(5)), parse_work_days("mon-fri"))
        self.assertEqual(frozenset({0, 1, 3}), parse_work_days("Monday, tue, thu"))
        self.assertEqual("mon-tue, thu", format_work_days(parse_work_days("thu, mon-tue")))
        for wrong in ("maandag", "fri-mon"):
            with self.assertRaises(ValueError):
                parse_work_days(wrong)

    def test_a_day_off_is_not_empty_and_not_shown(self):
        four_days = parse_work_days("mon-thu")
        days = [DaySummary("2026-10-22", 0, 0), DaySummary("2026-10-23", 0, 0)]
        self.assertEqual(["2026-10-22"], [d.day for d in visible(days, four_days)])
        self.assertEqual([("2026-10-22", "Thursday is empty")], attention(days, 8 * H, date(2026, 10, 24), four_days))


if __name__ == "__main__":
    unittest.main()
