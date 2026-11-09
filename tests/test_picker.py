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

    def test_jira_results_notify_on_text_so_the_list_can_resize(self):
        picker = controller(remote=lambda text: [])
        picker.on_text = mock.Mock()
        picker.search.stringValue.return_value = "kassa"
        picker.start_remote("kassa")
        picker.show_remote(picker.search_id, [PickItem("IN-9", "IN-9 · Kassa app", "Jira")], "")
        picker.on_text.assert_called_once_with()

    def test_an_older_answer_does_not_notify_on_text(self):
        picker = controller(remote=lambda text: [])
        picker.on_text = mock.Mock()
        picker.search.stringValue.return_value = "kass"
        picker.start_remote("kas")
        old = picker.search_id
        picker.start_remote("kass")
        picker.show_remote(old, [PickItem("IN-9", "IN-9 · Kassa app", "Jira")], "")
        picker.on_text.assert_not_called()

    def test_choose_fixes_the_selected_ticket_and_shows_its_name(self):
        picker = controller(remote=lambda text: [])
        picker.on_pick = mock.Mock()
        picker.search.stringValue.return_value = "kassa"
        picker.start_remote("kassa")
        self.assertEqual("IN-1", picker.choose().key)
        picker.search.setStringValue_.assert_called_with("IN-1 · Kassa")
        self.assertFalse(picker.pending)
        picker.on_pick.assert_called_once_with()

    def test_a_late_jira_answer_does_not_change_the_chosen_ticket(self):
        picker = controller(remote=lambda text: [])
        picker.search.stringValue.return_value = "kassa"
        picker.start_remote("kassa")
        waiting = picker.search_id
        picker.choose()
        picker.table.selectedRow.return_value = 1
        picker.show_remote(waiting, [PickItem("IN-9", "IN-9 · Kassa app", "Jira")], "")
        self.assertEqual("IN-1", picker.chosen.key)
        self.assertEqual("IN-1", picker.choose().key)

    def test_a_typed_key_puts_only_the_key_in_the_field(self):
        picker = controller()
        picker.apply_filter("IN-77")
        picker.choose()
        picker.search.setStringValue_.assert_called_with("IN-77")

    def test_typing_again_clears_the_choice(self):
        picker = controller()
        picker.search.stringValue.return_value = "kas"
        picker.choose()
        picker.controlTextDidChange_(None)
        self.assertIsNone(picker.chosen)

    def test_tab_from_the_search_field_chooses_when_on_pick_is_set(self):
        picker = controller()
        picker.on_pick = mock.Mock()
        picker.search.stringValue.return_value = "kas"
        picker.tab_order = (picker.search, mock.Mock())
        self.assertTrue(picker.control_textView_doCommandBySelector_(picker.search, None, b"insertTab:"))
        self.assertEqual("IN-1", picker.chosen.key)

    def test_tab_in_the_normal_picker_does_not_choose(self):
        picker = controller()
        picker.search.stringValue.return_value = "kas"
        picker.extra_field = mock.Mock()
        picker.control_textView_doCommandBySelector_(picker.search, None, b"insertTab:")
        self.assertIsNone(picker.chosen)

    def test_a_click_chooses_and_moves_on_to_the_duration(self):
        picker = controller()
        picker.on_pick = mock.Mock()
        picker.extra_field = mock.Mock()
        picker.table.clickedRow.return_value = 0
        picker.clicked_(None)
        self.assertEqual("IN-1", picker.chosen.key)
        picker.table.window().makeFirstResponder_.assert_called_with(picker.extra_field)

    def test_a_click_outside_the_rows_does_nothing(self):
        picker = controller()
        picker.on_pick = mock.Mock()
        picker.table.clickedRow.return_value = -1
        picker.clicked_(None)
        self.assertIsNone(picker.chosen)

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
