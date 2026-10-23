import unittest
from datetime import datetime

from tempo_tracker.store import Store
from tempo_tracker.tracker import (
    AWAY,
    PAUSED,
    TRACKING,
    Activity,
    CameBack,
    CheckinDue,
    DayStart,
    EndOfDay,
    Tracker,
)

TICKET = Activity("IN-1", "IN-1 · Kassa koppelen")
OTHER = Activity("IN-2", "IN-2 · Factuur bug")
MEETING = Activity.fixed("Meetings")
TICK = 15


def at(hour: int, minute: int = 0, day: int = 20) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.tracker = Tracker(self.store, checkin_seconds=45 * 60, idle_seconds=10 * 60, end_of_day_minutes=18 * 60)

    def advance(self, until: float, idle_since: float | None = None) -> list:
        """Tikt elke 15 s zoals de app; inactief vanaf `idle_since`. Geeft alle gebeurtenissen terug."""
        now = self.tracker.last_tick or until
        events = []
        while now < until:
            now = min(now + TICK, until)
            idle = now - idle_since if idle_since is not None and now >= idle_since else 0
            event = self.tracker.tick(now, idle)
            if event:
                events.append(event)
        return events

    def seconds(self, key: str, day: str = "2026-10-20") -> int:
        return next((r.seconds for r in self.store.unsent_rows(day) if r.issue_key == key), 0)

    def go_idle(self, start: float, idle_from: float, until: float) -> None:
        self.tracker.start(TICKET, start)
        self.assertEqual([], self.advance(until, idle_since=idle_from))

    def test_check_in_is_due_after_the_interval(self):
        self.tracker.start(TICKET, at(9))
        self.assertEqual([], self.advance(at(9, 44)))
        self.assertEqual([CheckinDue(at(9))], self.advance(at(9, 45)))

    def test_confirm_books_the_period_on_the_current_ticket(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 45))
        self.tracker.confirm(at(9, 45))
        self.assertEqual(45 * 60, self.seconds("IN-1"))
        self.assertEqual([], self.advance(at(10)))

    def test_other_ticket_books_the_period_on_the_new_ticket_and_continues_with_it(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.reassign(OTHER, at(9, 45))
        self.tracker.confirm(at(10, 30))
        self.assertEqual(0, self.seconds("IN-1"))
        self.assertEqual(90 * 60, self.seconds("IN-2"))

    def test_check_in_with_extras_books_the_rest_on_the_current_ticket(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.confirm(at(9, 45), ((OTHER, 15 * 60), (MEETING, 10 * 60)))
        self.assertEqual(20 * 60, self.seconds("IN-1"))
        self.assertEqual(15 * 60, self.seconds("IN-2"))
        self.assertEqual(10 * 60, self.seconds("@Meetings"))
        self.assertEqual(TICKET, self.tracker.current)

    def test_extras_never_exceed_the_period(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.confirm(at(9, 30), ((OTHER, 45 * 60),))
        self.assertEqual(0, self.seconds("IN-1"))
        self.assertEqual(30 * 60, self.seconds("IN-2"))

    def test_other_ticket_with_extras(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.reassign(OTHER, at(9, 45), ((MEETING, 15 * 60),))
        self.assertEqual((30 * 60, 15 * 60, 0), (self.seconds("IN-2"), self.seconds("@Meetings"), self.seconds("IN-1")))
        self.assertEqual(OTHER, self.tracker.current)

    def test_pause_with_extras(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.pause(at(9, 45), ((OTHER, 15 * 60),))
        self.assertEqual((30 * 60, 15 * 60), (self.seconds("IN-1"), self.seconds("IN-2")))

    def test_add_time_comes_out_of_the_open_period_and_keeps_the_check_in_timer(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 30))
        self.tracker.add_time(OTHER, 15 * 60, at(9, 30))
        self.assertEqual((15 * 60, 15 * 60), (self.seconds("IN-1"), self.seconds("IN-2")))
        self.assertEqual([CheckinDue(at(9))], self.advance(at(9, 45)))
        self.tracker.confirm(at(9, 45))
        self.assertEqual(30 * 60, self.seconds("IN-1"))

    def test_add_time_while_paused_adds_a_period_ending_now(self):
        self.tracker.add_time(OTHER, 20 * 60, at(17))
        rows = self.store.unsent_rows("2026-10-20")
        self.assertEqual([("IN-2", 20 * 60, at(16, 40))], [(r.issue_key, r.seconds, r.first_started_at) for r in rows])
        self.assertEqual(PAUSED, self.tracker.status)

    def test_start_from_an_earlier_time_books_that_time_and_starts_the_check_in_timer_now(self):
        self.tracker.start(TICKET, at(11, 15), since=at(9))
        self.assertEqual([], self.advance(at(11, 59)))
        self.assertEqual([CheckinDue(at(9))], self.advance(at(12)))
        self.tracker.confirm(at(12))
        self.assertEqual(3 * 3600, self.seconds("IN-1"))

    def test_switch_from_an_earlier_time_moves_that_part_to_the_new_ticket(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.start(OTHER, at(10), since=at(9, 40))
        self.tracker.pause(at(10, 30))
        self.assertEqual((40 * 60, 50 * 60), (self.seconds("IN-1"), self.seconds("IN-2")))

    def test_earliest_start_avoids_overlap_and_yesterday(self):
        self.assertEqual(at(0), self.tracker.earliest_start(at(9)))
        self.tracker.start(TICKET, at(9))
        self.tracker.pause(at(10))
        self.assertEqual(at(10), self.tracker.earliest_start(at(11)))
        self.tracker.start(OTHER, at(11))
        self.assertEqual(at(11), self.tracker.earliest_start(at(11, 30)))

    def test_discarded_time_does_not_block_an_earlier_start(self):
        self.tracker.start(MEETING, at(11))
        self.tracker.pause(at(11, 1))
        self.store.discard_unsent("2026-10-20", "@Meetings")
        self.assertEqual(at(0), self.tracker.earliest_start(at(11, 5)))

    def test_open_seconds(self):
        self.assertEqual(0, self.tracker.open_seconds(at(9)))
        self.tracker.start(TICKET, at(9))
        self.assertEqual(30 * 60, self.tracker.open_seconds(at(9, 30)))

    def test_switching_splits_the_time(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.start(MEETING, at(9, 20))
        self.tracker.pause(at(10))
        self.assertEqual(20 * 60, self.seconds("IN-1"))
        self.assertEqual(40 * 60, self.seconds("@Meetings"))

    def test_idle_closes_the_period_where_inactivity_began(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(9, 30))
        self.assertEqual(AWAY, self.tracker.status)
        self.assertEqual(18 * 60, self.seconds("IN-1"))

    def test_short_inactivity_is_not_away(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(9, 27))
        self.assertEqual(TRACKING, self.tracker.status)

    def test_coming_back_asks_about_the_away_period(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(9, 50))
        self.assertEqual([CameBack(at(9, 18), at(9, 50) + TICK)], self.advance(at(9, 50) + TICK))

    def test_away_period_can_be_booked_on_another_activity_and_tracking_resumes(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(10))
        self.tracker.resolve_away(MEETING, at(10))
        self.assertEqual(TRACKING, self.tracker.status)
        self.assertEqual(TICKET, self.tracker.current)
        self.assertEqual(42 * 60, self.seconds("@Meetings"))
        self.tracker.confirm(at(10, 45))
        self.assertEqual(63 * 60, self.seconds("IN-1"))

    def test_away_as_pause_books_nothing(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(10))
        self.tracker.resolve_away(None, at(10))
        self.assertEqual(18 * 60, self.seconds("IN-1"))
        self.assertEqual(1, len(self.store.unsent_rows("2026-10-20")))

    def test_away_resets_the_check_in_timer(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(10))
        self.tracker.resolve_away(None, at(10))
        self.assertEqual([], self.advance(at(10, 44)))
        self.assertEqual([CheckinDue(at(10))], self.advance(at(10, 45)))

    def test_stop_for_today_pauses(self):
        self.go_idle(at(9), idle_from=at(9, 18), until=at(10))
        self.tracker.resolve_away(None, at(10), resume=False)
        self.assertEqual(PAUSED, self.tracker.status)

    def test_sleep_is_treated_as_away_from_the_last_tick(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 10))
        self.assertEqual(CameBack(at(9, 10), at(11)), self.tracker.tick(at(11), 0))
        self.assertEqual(10 * 60, self.seconds("IN-1"))

    def test_a_long_open_dialog_is_not_mistaken_for_sleep(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 45))
        self.tracker.confirm(at(9, 50))
        self.assertEqual([], self.advance(at(9, 51)))
        self.assertEqual(TRACKING, self.tracker.status)

    def test_overnight_period_is_split_at_midnight(self):
        self.tracker.start(TICKET, at(23))
        self.tracker.confirm(at(1, day=21))
        self.assertEqual(3600, self.seconds("IN-1", "2026-10-20"))
        self.assertEqual(3600, self.seconds("IN-1", "2026-10-21"))

    def test_day_start_is_asked_once_per_day_when_paused(self):
        self.assertEqual(DayStart(), self.tracker.tick(at(8), 0))
        self.assertEqual([], self.advance(at(8, 30)))
        self.assertEqual(DayStart(), self.tracker.tick(at(8, day=21), 0))

    def test_day_start_is_not_asked_while_idle_or_after_starting(self):
        self.assertIsNone(self.tracker.tick(at(8), 30 * 60))
        self.tracker.start(TICKET, at(8, 5))
        self.assertEqual([], self.advance(at(8, 30)))

    def test_end_of_day_once_and_only_with_tracked_time(self):
        self.store.set_state("start_prompted", "2026-10-20")
        self.assertIsNone(self.tracker.tick(at(18, 1), 0))
        self.tracker.start(TICKET, at(18, 1))
        self.tracker.pause(at(18, 2))
        self.assertEqual([EndOfDay("2026-10-20")], self.advance(at(18, 10)))

    def test_end_of_day_waits_until_you_are_back(self):
        self.tracker.start(TICKET, at(17, 30))
        self.assertEqual([], self.advance(at(18, 20), idle_since=at(17, 45)))
        back_at = at(18, 20) + TICK
        self.assertEqual(CameBack(at(17, 45), back_at), self.tracker.tick(back_at, 0))
        self.tracker.resolve_away(None, back_at, resume=False)
        self.assertEqual(EndOfDay("2026-10-20"), self.tracker.tick(back_at + TICK, 0))

    def test_restart_shortly_after_continues_tracking(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 10))
        restarted = Tracker(self.store, 45 * 60, 10 * 60, 18 * 60)
        restarted.restore(at(9, 11))
        self.assertEqual(TRACKING, restarted.status)
        restarted.confirm(at(9, 45))
        self.assertEqual(45 * 60, self.seconds("IN-1"))

    def test_restart_after_a_long_gap_treats_it_as_away(self):
        self.tracker.start(TICKET, at(9))
        self.advance(at(9, 10))
        restarted = Tracker(self.store, 45 * 60, 10 * 60, 18 * 60)
        restarted.restore(at(11))
        self.assertEqual(AWAY, restarted.status)
        self.assertEqual(10 * 60, self.seconds("IN-1"))

    def test_seconds_today_includes_the_open_period(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.confirm(at(9, 45))
        self.assertEqual(60 * 60, self.tracker.seconds_today(at(10)))



class PresenceAndNewDayTest(unittest.TestCase):
    """Scherm uit of vergrendeld telt als weg; een afwezigheid die over middernacht loopt, vraagt niets meer over gisteren."""

    seconds = TrackerTest.seconds

    def setUp(self):
        self.store = Store(":memory:")
        self.tracker = Tracker(self.store, checkin_seconds=45 * 60, idle_seconds=10 * 60, end_of_day_minutes=18 * 60)

    def test_screen_off_is_away_even_with_recent_input(self):
        self.tracker.start(TICKET, at(9))
        self.tracker.touch(at(9, 29))  # de ticks tussendoor
        self.assertIsNone(self.tracker.tick(at(9, 30), 5, present=False))
        self.assertEqual((AWAY, at(9, 30) - 5), (self.tracker.status, self.tracker.away_since))
        self.assertIsNone(self.tracker.tick(at(9, 31), 0, present=False))  # half wakker: geen 'welcome back'

    def test_away_since_yesterday_stops_quietly_and_the_day_starts_fresh(self):
        self.tracker.start(TICKET, at(17, day=21))
        self.tracker.go_away(at(19, 24, day=21))
        self.assertEqual(DayStart(), self.tracker.tick(at(8, 10, day=22), 0))
        self.assertEqual(PAUSED, self.tracker.status)
        self.assertEqual(int(2.4 * 3600), self.seconds("IN-1", "2026-10-21"))
        self.assertEqual(0, self.seconds("IN-1", "2026-10-22"))

    def test_no_good_morning_on_other_days(self):
        self.assertIsNone(self.tracker.tick(at(10, day=24), 0))  # zaterdag
        self.tracker.work_days = {0, 1, 2, 3, 4, 5}
        self.assertEqual(DayStart(), self.tracker.tick(at(10, 1, day=24), 0))


class CarveTest(unittest.TestCase):
    """Een gesprek midden in de open periode: die tijd gaat naar de meeting, de rest blijft op het ticket."""

    seconds = TrackerTest.seconds

    def setUp(self):
        self.store = Store(":memory:")
        self.tracker = Tracker(self.store, checkin_seconds=45 * 60, idle_seconds=10 * 60, end_of_day_minutes=18 * 60)
        self.tracker.start(TICKET, at(9))
        self.tracker.carve(MEETING, at(9, 30), at(9, 47))

    def test_the_call_is_booked_at_once_and_left_out_of_the_open_period(self):
        self.assertEqual(17 * 60, self.seconds("@Meetings"))
        self.assertEqual(30 * 60 + 13 * 60, self.tracker.open_seconds(at(10)))
        self.assertEqual(60 * 60, self.tracker.seconds_today(at(10)))

    def test_check_in_books_the_rest_around_the_call(self):
        self.tracker.confirm(at(10))
        self.assertEqual((43 * 60, 17 * 60), (self.seconds("IN-1"), self.seconds("@Meetings")))
        self.assertEqual([], self.tracker.carved)

    def test_extras_skip_the_call(self):
        self.tracker.confirm(at(10), ((OTHER, 20 * 60),))  # 9:47–10:00 en 9:23–9:30
        self.assertEqual((23 * 60, 20 * 60, 17 * 60), (self.seconds("IN-1"), self.seconds("IN-2"), self.seconds("@Meetings")))

    def test_other_ticket_for_the_whole_period_keeps_the_call(self):
        self.tracker.reassign(OTHER, at(10))
        self.assertEqual((0, 43 * 60, 17 * 60), (self.seconds("IN-1"), self.seconds("IN-2"), self.seconds("@Meetings")))

    def test_a_switch_before_the_call_keeps_it_out_of_the_new_ticket(self):
        self.tracker.start(OTHER, at(9, 50), since=at(9, 15))
        self.tracker.confirm(at(10))
        self.assertEqual((15 * 60, 28 * 60, 17 * 60), (self.seconds("IN-1"), self.seconds("IN-2"), self.seconds("@Meetings")))

    def test_survives_a_restart(self):
        self.tracker.touch(at(9, 47))
        self.tracker.confirm(at(9, 47))  # slaat de stand op, het gesprek blijft eruit
        self.tracker.carve(MEETING, at(9, 50), at(9, 55))
        restarted = Tracker(self.store, 45 * 60, 10 * 60, 18 * 60)
        restarted.restore(at(9, 48))
        restarted.confirm(at(10))
        self.assertEqual((38 * 60, 22 * 60), (self.seconds("IN-1"), self.seconds("@Meetings")))

    def test_only_during_tracking_and_not_before_the_open_period(self):
        self.tracker.carve(OTHER, at(8), at(9, 5))
        self.assertEqual(5 * 60, self.seconds("IN-2"))
        self.tracker.pause(at(10))
        self.tracker.carve(OTHER, at(10), at(10, 30))
        self.assertEqual(5 * 60, self.seconds("IN-2"))


if __name__ == "__main__":
    unittest.main()
