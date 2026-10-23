import unittest
from unittest import mock

from tempo_tracker import dialogs
from tempo_tracker.dialogs import PickItem

LOCAL = [PickItem("IN-1", "IN-1 · Kassa", "assigned"), PickItem("@Meetings", "Meetings", "fixed")]


def controller(remote=None):
    picker = dialogs._PickerController.alloc().initWithItems_remote_(LOCAL, remote)
    picker.table, picker.search, picker.status = mock.Mock(), mock.Mock(), mock.Mock()
    picker.table.selectedRow.return_value = 0
    return picker


class PickerTest(unittest.TestCase):
    def test_jira_results_come_after_the_local_matches_without_duplicates(self):
        picker = controller(remote=lambda text: [])
        picker.search.stringValue.return_value = "kassa"
        picker.start_remote("kassa")
        picker.show_remote(picker.search_id, [PickItem("IN-1", "IN-1 · Kassa", "Jira"), PickItem("IN-9", "IN-9 · Kassa app", "Jira")], "")
        self.assertEqual(["IN-1", "IN-9"], [i.key for i in picker.items])
        self.assertEqual("assigned", picker.items[0].hint)

    def test_an_older_answer_is_ignored(self):
        picker = controller(remote=lambda text: [])
        picker.search.stringValue.return_value = "zzz"
        picker.start_remote("kas")
        old = picker.search_id
        picker.start_remote("kass")
        picker.show_remote(old, [PickItem("IN-9", "IN-9 · Kassa app", "Jira")], "")
        self.assertEqual(LOCAL, picker.items)  # het antwoord van "kas" is niet getoond
        self.assertTrue(picker.pending)

    def test_short_text_does_not_search_jira(self):
        picker = controller(remote=mock.Mock())
        picker.start_remote("ka")
        self.assertFalse(picker.pending)

    def test_a_failed_search_keeps_the_local_results_and_says_so(self):
        picker = controller(remote=lambda text: [])
        picker.search.stringValue.return_value = "kassa"
        picker.start_remote("kassa")
        picker.show_remote(picker.search_id, [], "Jira unreachable")
        self.assertEqual(["IN-1"], [i.key for i in picker.items])
        picker.status.setStringValue_.assert_called_with("Jira search failed: Jira unreachable")

    def test_enter_without_matches_keeps_the_window_open(self):
        picker = controller()
        picker.items = []
        with mock.patch.object(dialogs, "NSApp") as app, mock.patch.object(dialogs, "NSBeep"):
            self.assertTrue(picker.control_textView_doCommandBySelector_(None, None, b"insertNewline:"))
        app.stopModalWithCode_.assert_not_called()

    def test_enter_with_a_match_chooses_it(self):
        picker = controller()
        with mock.patch.object(dialogs, "NSApp") as app:
            picker.control_textView_doCommandBySelector_(None, None, b"insertNewline:")
        app.stopModalWithCode_.assert_called_once_with(0)
