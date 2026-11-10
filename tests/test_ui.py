import unittest
from unittest import mock

from AppKit import NSMakeSize, NSSegmentedControl

from tempo_tracker import dialogs, ui
from tempo_tracker.dialogs import FormField


def find(view, identifier):
    if view.identifier() == identifier:
        return view
    for child in view.subviews():
        found = find(child, identifier)
        if found is not None:
            return found
    return None


class MenuHeaderTest(unittest.TestCase):
    def test_stretches_with_a_wider_menu(self):
        header = ui.MenuHeader.alloc().init()
        header.setFrameSize_(NSMakeSize(500, ui.MenuHeader.HEIGHT))
        self.assertEqual(500 - 32, header.progress.frame().size.width)
        total = header.total.frame()
        self.assertEqual(500 - 16, total.origin.x + total.size.width)  # stays right-aligned above the end of the bar


def find_by_value(view, value):
    if hasattr(view, "stringValue") and view.stringValue() == value and view.isEditable():
        return view
    for child in view.subviews():
        found = find_by_value(child, value)
        if found is not None:
            return found
    return None


class FormLinesTest(unittest.TestCase):
    def test_a_field_with_more_lines_is_taller_and_wraps(self):
        options = "Standup=DEMO-100, Refinement=DEMO-101, Support=DEMO-7"
        seen = {}

        def fake_run(window, default_button=None, timeout=None):
            window.layoutIfNeeded()
            seen["tall"] = find_by_value(window.contentView(), options)
            seen["short"] = find_by_value(window.contentView(), "me@example.com")
            return 0

        fields = [FormField("email", "Jira email", "me@example.com"), FormField("fixed", "Fixed options", options, lines=4)]
        with mock.patch.object(ui, "run_modal", side_effect=fake_run):
            values = dialogs.form("Settings", "", fields)
        self.assertEqual(options, values["fixed"])
        self.assertGreater(seen["tall"].frame().size.height, 3 * seen["short"].frame().size.height)
        self.assertTrue(seen["tall"].cell().wraps())
        self.assertFalse(seen["tall"].cell().isScrollable())  # wraps instead of scrolling sideways
        self.assertFalse(seen["tall"].usesSingleLineMode())


class FormChoicesTest(unittest.TestCase):
    FIELDS = [
        FormField("email", "Jira email", "me@example.com"),
        FormField("split", "Tempo worklogs", "ticket", choices=(("ticket", "Per ticket"), ("period", "Per period"))),
    ]

    def run_form(self, choose=None):
        seen = {}

        def fake_run(window, default_button=None, timeout=None):
            switch = find(window.contentView(), "split")
            seen["switch"], seen["selected"] = switch, switch.selectedSegment()
            if choose is not None:
                switch.setSelectedSegment_(choose)
            return 0  # Save

        with mock.patch.object(ui, "run_modal", side_effect=fake_run):
            values = dialogs.form("Settings", "", self.FIELDS)
        return values, seen

    def test_a_choice_field_is_a_switch_that_opens_on_the_current_value(self):
        values, seen = self.run_form()
        self.assertIsInstance(seen["switch"], NSSegmentedControl)
        self.assertEqual(0, seen["selected"])
        self.assertEqual({"email": "me@example.com", "split": "ticket"}, values)

    def test_the_chosen_value_is_returned(self):
        values, _ = self.run_form(choose=1)
        self.assertEqual("period", values["split"])
