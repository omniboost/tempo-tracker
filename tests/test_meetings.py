import unittest
from datetime import datetime

from tempo_tracker.meetings import suggest
from tempo_tracker.store import Store


def at(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


def meeting(day: int, hour: int = 9, minute: int = 30, key: str = "@Standup") -> tuple[float, str, str]:
    return at(day, hour, minute), key, key[1:]


class SuggestTest(unittest.TestCase):
    """Oktober 2026: de 21e is een woensdag."""

    def test_the_last_two_times_the_same_meeting(self):
        self.assertEqual(("@Standup", "Standup"), suggest([meeting(20), meeting(19, 9, 40)], at(21, 9, 31)))

    def test_one_time_is_not_enough(self):
        self.assertIsNone(suggest([meeting(20)], at(21, 9, 30)))

    def test_a_different_choice_last_time_means_no_suggestion(self):
        self.assertIsNone(suggest([meeting(20, key="@Retro"), meeting(19), meeting(16)], at(21, 9, 30)))

    def test_only_around_the_same_time(self):
        self.assertIsNone(suggest([meeting(20, 9, 50), meeting(19)], at(21, 9, 30)))

    def test_same_weekday_up_to_four_weeks_back(self):
        refinement = [meeting(14, 14, 0, "@Refinement"), meeting(7, 14, 0, "@Refinement")]
        self.assertEqual(("@Refinement", "Refinement"), suggest(refinement, at(21, 14)))
        self.assertIsNone(suggest([meeting(13, 14, 0), meeting(12, 14, 0)], at(21, 14)))  # andere weekdag, > 1 week

    def test_one_per_day_and_not_today(self):
        self.assertIsNone(suggest([meeting(21, 9, 0), meeting(20, 9, 35), meeting(20, 9, 25)], at(21, 9, 30)))


class StoreTest(unittest.TestCase):
    def test_prune_removes_only_fully_sent_old_days(self):
        store = Store(":memory:")
        for day in (1, 2, 3, 25):
            store.add_segment("IN-1", "IN-1", at(day, 9), at(day, 10))
        store.record_worklog("2026-10-01", "IN-1", 3600, "", "w-1")
        store.record_worklog("2026-10-25", "IN-1", 3600, "", "w-2")
        store.record_worklog("2026-10-03", "IN-1", 3600, "", "w-3")
        store.save_draft("2026-10-03", "IN-9", "IN-9", 1800, 0, "")  # nog een open handmatige regel

        self.assertEqual(1, store.prune("2026-10-20"))
        self.assertEqual({"w-2", "w-3"}, store.own_worklog_ids())
        self.assertEqual(["2026-10-02"], store.days_with_unsent())  # niet verstuurd: blijft
        self.assertEqual(0, store.prune("2026-10-20"))

    def test_fixed_segments_are_short_pieces_on_fixed_options(self):
        store = Store(":memory:")
        store.add_segment("@Standup", "Standup", at(20, 9, 30), at(20, 9, 45))
        store.add_segment("@Support", "Support", at(20, 10), at(20, 14))
        store.add_segment("IN-1", "IN-1", at(20, 14), at(20, 14, 30))
        self.assertEqual([(at(20, 9, 30), "@Standup", "Standup")], store.fixed_segments(at(1, 0), 2 * 3600))


if __name__ == "__main__":
    unittest.main()
