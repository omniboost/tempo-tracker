import unittest
from datetime import datetime
from unittest import mock

from AppKit import NSMakePoint

from tempo_tracker import dialogs
from tempo_tracker.config import issue_url
from tempo_tracker.dialogs import _parse_clock
from tempo_tracker.store import Store
from tempo_tracker.timeline import TEMPO, TRACKED, Block, entries, free_start, gaps, merge, overlaps, split

DAY = "2026-10-19"


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 19, hour, minute).timestamp()


def tracked(key: str, start: float, end: float) -> Block:
    return Block(start, end, key, key, TRACKED)


class MergeTest(unittest.TestCase):
    def test_consecutive_pieces_of_one_ticket_become_one_block(self):
        merged = merge([tracked("IN-1", at(9), at(9, 30)), tracked("IN-1", at(9, 30), at(10)), tracked("IN-2", at(10), at(11))])
        self.assertEqual([(at(9), at(10), "IN-1"), (at(10), at(11), "IN-2")], [(b.start, b.end, b.key) for b in merged])

    def test_a_gap_or_another_lane_keeps_blocks_apart(self):
        merged = merge([
            tracked("IN-1", at(9), at(9, 30)),
            tracked("IN-1", at(9, 35), at(10)),
            Block(at(10), at(11), "IN-1", "IN-1", TEMPO),
        ])
        self.assertEqual(3, len(merged))


class EntriesTest(unittest.TestCase):
    def test_blocks_and_gaps_in_time_order(self):
        meeting = Block(at(14), at(14, 30), "IN-9", "meeting", TEMPO)
        blocks = [tracked("IN-2", at(13), at(15)), meeting, tracked("IN-1", at(9), at(12))]
        self.assertEqual(
            [(at(9), "IN-1"), (at(12), None), (at(13), "IN-2"), (at(14), "IN-9")],
            [(start, block.key if block else None) for start, _, block in entries(blocks)],
        )

    def test_a_filled_gap_is_no_gap_anymore(self):
        blocks = [tracked("IN-1", at(9), at(12)), tracked("IN-2", at(13), at(14))]
        filled = blocks + [Block(at(12), at(13), "IN-9", "IN-9", TRACKED, pending=True)]
        self.assertEqual(1, sum(1 for _, _, block in entries(blocks) if block is None))
        self.assertEqual(0, sum(1 for _, _, block in entries(filled) if block is None))


class GapsTest(unittest.TestCase):
    def test_time_between_blocks_without_tracking_or_tempo_is_a_gap(self):
        blocks = [tracked("IN-1", at(9), at(12, 15)), Block(at(14), at(15), "IN-9", "meeting", TEMPO), tracked("IN-2", at(13), at(14, 30))]
        self.assertEqual([(at(12, 15), at(13))], gaps(blocks))

    def test_short_pauses_and_time_before_or_after_the_day_are_no_gaps(self):
        self.assertEqual([], gaps([tracked("IN-1", at(9), at(10)), tracked("IN-2", at(10, 5), at(11))]))
        self.assertEqual([], gaps([]))

    def test_a_long_block_covers_shorter_ones_inside_it(self):
        blocks = [tracked("IN-1", at(9), at(17)), Block(at(10), at(11), "IN-9", "meeting", TEMPO), tracked("IN-2", at(17, 30), at(18))]
        self.assertEqual([(at(17), at(17, 30))], gaps(blocks))


class FreeStartTest(unittest.TestCase):
    def setUp(self):
        self.blocks = [tracked("IN-1", at(9), at(12, 15)), tracked("IN-2", at(13), at(17, 15))]

    def test_the_first_gap_that_fits(self):
        self.assertEqual(at(12, 15), free_start(DAY, self.blocks, 30 * 60))

    def test_after_the_last_block_when_no_gap_fits(self):
        self.assertEqual(at(17, 15), free_start(DAY, self.blocks, 60 * 60))

    def test_nine_on_an_empty_day(self):
        self.assertEqual(at(9), free_start(DAY, [], 30 * 60))


class OverlapsTest(unittest.TestCase):
    def test_blocks_that_touch_do_not_overlap(self):
        blocks = [tracked("IN-1", at(9), at(10)), tracked("IN-2", at(11), at(12))]
        self.assertEqual([], overlaps(blocks, at(10), at(11)))

    def test_partly_inside_counts(self):
        blocks = [tracked("IN-1", at(9), at(10)), Block(at(10, 30), at(11, 30), "IN-9", "meeting", TEMPO)]
        self.assertEqual(["IN-1", "IN-9"], [b.key for b in overlaps(blocks, at(9, 45), at(10, 45))])


class SplitTest(unittest.TestCase):
    def test_one_worklog_per_period(self):
        periods = [(at(9), at(9, 30)), (at(9, 45), at(12, 15))]
        self.assertEqual([(at(9), 30 * 60), (at(9, 45), 150 * 60)], split(periods, [], 180 * 60))

    def test_pieces_that_touch_are_one_period(self):
        self.assertEqual([(at(9), 60 * 60)], split([(at(9), at(9, 30)), (at(9, 30), at(10))], [], 60 * 60))

    def test_a_meeting_in_tempo_is_cut_out(self):
        self.assertEqual(
            [(at(13, 50), 10 * 60), (at(15), 30 * 60)], split([(at(13, 50), at(15, 30))], [(at(14), at(15))], 40 * 60)
        )

    def test_short_pieces_join_the_nearest_period(self):
        periods = [(at(9), at(10)), (at(10, 30), at(10, 32)), (at(13), at(14))]
        self.assertEqual([(at(9), 62 * 60), (at(13), 60 * 60)], split(periods, [], 122 * 60))

    def test_more_time_goes_on_the_last_period(self):
        self.assertEqual([(at(9), 30 * 60), (at(13), 45 * 60)], split([(at(9), at(9, 30)), (at(13), at(13, 40))], [], 75 * 60))

    def test_less_time_comes_off_the_last_period_first(self):
        periods = [(at(9), at(10)), (at(13), at(13, 20))]
        self.assertEqual([(at(9), 50 * 60)], split(periods, [], 50 * 60))

    def test_seconds_are_rounded_to_minutes_and_the_total_stays_exact(self):
        periods = [(at(9), at(9, 20) + 29), (at(10), at(10, 20) + 40)]
        parts = split(periods, [], 45 * 60)
        self.assertEqual(45 * 60, sum(length for _, length in parts))
        self.assertTrue(all(length % 60 == 0 for _, length in parts))

    def test_no_periods_or_no_time(self):
        self.assertEqual([], split([], [], 30 * 60))
        self.assertEqual([], split([(at(9), at(10))], [], 0))


class ParseClockTest(unittest.TestCase):
    def test_the_ways_to_type_a_start_time(self):
        for text in ("14:30", "14.30", "1430"):
            self.assertEqual(at(14, 30), _parse_clock(DAY, text))
        self.assertEqual(at(9, 5), _parse_clock(DAY, "9:05"))
        self.assertEqual(at(9), _parse_clock(DAY, "9"))

    def test_no_time(self):
        for text in ("", "25:00", "9:75", "later"):
            self.assertIsNone(_parse_clock(DAY, text))


class IssueUrlTest(unittest.TestCase):
    def test_a_ticket_opens_on_the_jira_site(self):
        self.assertEqual("https://company.atlassian.net/browse/IN-123", issue_url("company.atlassian.net", " IN-123 "))

    def test_no_link_without_a_ticket_or_a_site(self):
        for site, key in (("company.atlassian.net", "@Standup"), ("company.atlassian.net", ""), ("company.atlassian.net", "in-1"), ("", "IN-1")):
            self.assertIsNone(issue_url(site, key))


class NameAndKeyTest(unittest.TestCase):
    """The day overview shows a ticket as its name first, then its key."""

    def test_name_first_then_key(self):
        self.assertEqual(("Kassa koppelen", "IN-123"), dialogs._name_and_key("IN-123 · Kassa koppelen"))
        self.assertEqual(("Support Work", "IN-50"), dialogs._name_and_key("Support Work", "IN-50"))
        self.assertEqual(("", "IN-7"), dialogs._name_and_key("IN-7"))
        self.assertEqual(("Standup", ""), dialogs._name_and_key("Standup"))

    def test_a_long_name_is_cut_off_but_the_key_stays(self):
        name = dialogs.ui.name_text("A very long ticket title " * 10)
        key = dialogs.ui.key_text("IN-123", link=True)
        name_width, key_x = dialogs._name_then_key(name, key, 280)
        self.assertLessEqual(key_x + key.size().width + 2, 280)
        self.assertEqual(name_width + dialogs._NAME_KEY_GAP, key_x)

    def test_a_short_name_keeps_its_width(self):
        name = dialogs.ui.name_text("Bug")
        name_width, key_x = dialogs._name_then_key(name, dialogs.ui.key_text("IN-1"), 280)
        self.assertLess(name_width, 60)


class CalendarClickTest(unittest.TestCase):
    """A click on a ticket in the Calendar view opens it in Jira; a click on a gap fills it. No browser opens in tests."""

    def setUp(self):
        blocks = [tracked("IN-1", at(9), at(10)), Block(at(10), at(11), "@Standup", "Standup", TRACKED), tracked("IN-2", at(12), at(13))]
        _, self.calendar = dialogs._calendar(DAY, blocks, 600, "company.atlassian.net")
        self.calendar.on_gap = mock.Mock()
        self.view = self.calendar.view

    def click(self, key):
        row = next(r for r in self.view.rows if (r[2].key if r[2] else None) == key)
        box = self.view.card_rect(row)
        with mock.patch.object(dialogs, "_open_issue") as open_issue:
            self.view.click_at(NSMakePoint(box.origin.x + 10, box.origin.y + 10))
        return open_issue

    def test_a_ticket_opens_in_jira(self):
        self.click("IN-2").assert_called_once_with("company.atlassian.net", "IN-2")
        self.calendar.on_gap.assert_not_called()

    def test_a_fixed_option_without_a_key_opens_nothing(self):
        self.click("@Standup").assert_not_called()

    def test_a_fixed_option_opens_its_jira_key(self):
        blocks = [Block(at(9), at(10), "@Support Work", "Support Work", TRACKED, jira_key="IN-50")]
        _, self.calendar = dialogs._calendar(DAY, blocks, 600, "company.atlassian.net")
        self.view = self.calendar.view
        self.click("@Support Work").assert_called_once_with("company.atlassian.net", "IN-50")

    def test_a_gap_is_filled_not_opened(self):
        self.click(None).assert_not_called()
        self.calendar.on_gap.assert_called_once_with(at(11), at(12))

    def test_open_issue_hands_the_url_to_the_browser(self):
        with mock.patch("AppKit.NSWorkspace") as workspace:
            self.assertTrue(dialogs._open_issue("company.atlassian.net", "IN-2"))
            self.assertFalse(dialogs._open_issue("company.atlassian.net", "@Standup"))
        (url,), _ = workspace.sharedWorkspace.return_value.openURL_.call_args
        self.assertEqual("https://company.atlassian.net/browse/IN-2", url.absoluteString())
        workspace.sharedWorkspace.return_value.openURL_.assert_called_once()


class StoreSegmentsTest(unittest.TestCase):
    def test_segments_of_a_day_include_sent_but_not_discarded_time(self):
        store = Store(":memory:")
        store.add_segment("IN-1", "IN-1 · Kassa", at(9), at(10))
        store.add_segment("IN-2", "IN-2 · Bug", at(10), at(11))
        store.add_segment("IN-3", "IN-3 · Weg", at(11), at(12))
        store.record_worklog(DAY, "IN-1", 3600, "", "w-1")
        store.discard_unsent(DAY, "IN-3")
        self.assertEqual(
            [("IN-1", "IN-1 · Kassa", at(9), at(10)), ("IN-2", "IN-2 · Bug", at(10), at(11))], store.segments(DAY)
        )
        self.assertEqual({"w-1": "IN-1"}, store.own_worklog_keys())

    def test_a_period_over_midnight_shows_on_both_days(self):
        store = Store(":memory:")
        store.add_segment("IN-1", "IN-1", at(23), at(23) + 2 * 3600)
        self.assertEqual([(at(23), at(23) + 3600)], [(s[2], s[3]) for s in store.segments(DAY)])
        self.assertEqual(1, len(store.segments("2026-10-20")))
