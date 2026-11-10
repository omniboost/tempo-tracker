"""De vensters van de app, gebouwd met de bouwstenen uit ui.py."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

import objc
from AppKit import (
    NSAttributedString,
    NSBeep,
    NSBezierPath,
    NSFontAttributeName,
    NSGraphicsContext,
    NSForegroundColorAttributeName,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSMenu,
    NSMenuItem,
    NSLayoutAttributeLastBaseline,
    NSButton,
    NSApp,
    NSBox,
    NSColor,
    NSControlSizeLarge,
    NSNoBorder,
    NSFont,
    NSMakeRect,
    NSScrollView,
    NSSearchField,
    NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne,
    NSSecureTextField,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSView,
    NSWindowAnimationBehaviorNone,
)
from Foundation import NSObject

from . import timeline, ui
from .config import ISSUE_KEY_RE, issue_url
from .summary import parse_duration


@dataclass(frozen=True)
class PickItem:
    key: str
    label: str
    hint: str


TIMED_OUT = ui.TIMED_OUT
bring_to_front = ui.bring_to_front


def install_edit_menu() -> None:
    """Menubalk-apps hebben geen Wijzig-menu, waardoor ⌘V/⌘C/⌘A niet werken in tekstvelden. Dit menu
    is onzichtbaar maar zorgt dat die toetscombinaties bij het actieve veld aankomen."""
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key, shift in (
        ("Undo", "undo:", "z", False),
        ("Redo", "redo:", "z", True),
        ("Cut", "cut:", "x", False),
        ("Copy", "copy:", "c", False),
        ("Paste", "paste:", "v", False),
        ("Select All", "selectAll:", "a", False),
    ):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | (NSEventModifierFlagShift if shift else 0))
        edit.addItem_(item)
    edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    edit_item.setSubmenu_(edit)
    main = NSMenu.alloc().initWithTitle_("")
    main.addItem_(edit_item)
    NSApp.setMainMenu_(main)


def _button_row(card: ui.Card, buttons: list[str], cancel: int | None = None) -> None:
    """Eerste knop = hoofdknop (rechts, blauw); de rest links daarvan."""
    views = [ui.button(title, n, primary=n == 0) for n, title in enumerate(buttons)]
    card.buttons(list(reversed(views)), cancel_code=cancel)


def message(title: str, text: str) -> None:
    card = ui.Card(title)
    card.text(text)
    _button_row(card, ["OK"], cancel=0)
    card.run()


def choose(title: str, text: str, buttons: list[str], timeout: float | None = None) -> int:
    """Simpele keuze; Enter = eerste knop, Esc = laatste. Na `timeout` seconden zonder antwoord: TIMED_OUT."""
    card = ui.Card(title)
    card.text(text)
    _button_row(card, buttons, cancel=len(buttons) - 1 if len(buttons) > 1 else 0)
    return card.run(timeout)


def progress(title: str, text: str, work: Callable):
    """Kaart met een wieltje terwijl `work(report)` op de achtergrond loopt, zodat de app blijft reageren.

    `report(tekst)` werkt de regel onder de titel bij. Geeft terug wat `work` teruggeeft; een fout uit `work`
    wordt hier opnieuw gegooid.
    """
    import threading

    card = ui.Card(title)
    status = ui.label(text, 13, color=ui.SECONDARY_TEXT)
    card.add(ui.row(ui.spinner(), status, spacing=10), full_width=True)
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["value"] = work(lambda line: ui.on_main(status.setStringValue_, line))
        except Exception as error:  # naar de hoofdthread, daar afhandelen
            outcome["error"] = error
        finally:
            ui.on_main(NSApp.stopModalWithCode_, 0)

    threading.Thread(target=run, daemon=True).start()
    card.run()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


CHECKIN_YES, CHECKIN_PLUS, CHECKIN_OTHER, CHECKIN_BREAK, CHECKIN_LATER = range(5)


def checkin(ticket: str, period: str, since: str, split: list[tuple[str, str]] = (), timeout: float | None = None) -> int:
    """'Still working on …?': het ticket groot, de tijd sinds de vorige check-in, en de keuzes.

    `split`: al afgesplitste stukken [(ticket, duur)], plus de rest. Geeft een CHECKIN_*-code of TIMED_OUT.
    """
    card = ui.Card("Still working on", icon=True)
    card.add(ui.ticket_label(ticket, 17, ui.SEMIBOLD, wrap_width=card.inner), after=10)
    card.add(ui.row(
        ui.label(period, fnt=ui.digits(30, ui.SEMIBOLD)),
        ui.label(f"since {since}", 13, color=ui.SECONDARY_TEXT),
        spacing=10, align=NSLayoutAttributeLastBaseline,
    ))
    if split:
        card.add(ui.column(*[
            ui.row(ui.ticket_label(name, 12.5), ui.spacer(), ui.label(duration, fnt=ui.digits(12.5, ui.MEDIUM), color=ui.SECONDARY_TEXT))
            for name, duration in split
        ], spacing=4), full_width=True)
    card.buttons(
        [ui.button("Other ticket…", CHECKIN_OTHER), ui.button("Plus something else…", CHECKIN_PLUS), ui.button("Yes", CHECKIN_YES, primary=True)],
        secondary=[ui.link_button("Take a break", CHECKIN_BREAK, "pause.circle"), ui.link_button("Ask again in 15 min", CHECKIN_LATER, "clock.arrow.circlepath")],
        cancel_code=CHECKIN_LATER,
    )
    return card.run(timeout)


@dataclass
class WeekRow:
    day: str  # "Mon 19"
    total: str  # "7:45"
    status: str  # "✓ sent", "2:30 not sent", …
    kind: str  # "done" | "open" | "empty" | "future"


def week(title: str, text: str, rows: list[WeekRow], summary: str, fraction: float, notes: list[str], buttons: list[str]) -> int:
    """Weekoverzicht: een balk voor de hele week, een regel per dag en wat aandacht nodig heeft. Geeft de knopindex."""
    card = ui.Card(title, text, width=460)
    card.add(ui.row(ui.label(summary, 13, ui.MEDIUM), spacing=8), after=8)
    progress = ui.bar(8)
    progress.set_segments([(fraction, ui.ACCENT_FILL)])
    card.add(progress, full_width=True, after=14)
    colors = {"done": ui.SUCCESS, "open": ui.WARNING, "empty": ui.TERTIARY_TEXT, "future": ui.TERTIARY_TEXT}
    for row in rows:
        day = ui.label(row.day, 13, ui.MEDIUM)
        ui.constrain(day, width=70)
        total = ui.label(row.total, fnt=ui.digits(13), color=ui.SECONDARY_TEXT)
        total.setAlignment_(2)
        ui.constrain(total, width=50)
        card.add(ui.row(day, total, ui.label(row.status, 12.5, color=colors.get(row.kind, ui.SECONDARY_TEXT)), spacing=14), after=4)
    if notes:
        card.stack.setCustomSpacing_afterView_(14, card.stack.arrangedSubviews()[-1])
        for note in notes:
            card.add(ui.row(ui.symbol("exclamationmark.circle", 12, ui.WARNING), ui.label(note, 12.5, color=ui.WARNING), spacing=6), after=4)
    _button_row(card, buttons, cancel=len(buttons) - 1)
    return card.run()


# -- ticketkiezer ------------------------------------------------------------


def _selector_name(selector) -> str:
    return selector.decode() if isinstance(selector, bytes) else str(selector)


REMOTE_MIN_CHARS = 3  # zoeken in Jira pas vanaf zoveel tekens
REMOTE_DELAY = 0.3  # seconden stilte na een toetsaanslag voordat Jira gevraagd wordt


class _PickerController(NSObject):
    def initWithItems_remote_(self, items, remote):
        self = objc.super(_PickerController, self).init()
        if self is None:
            return None
        self.all_items = list(items)
        self.items = list(items)
        self.remote_search = remote  # tekst -> lijst PickItem uit Jira, of None
        self.remote_items = []
        self.search_id = 0  # verhoogt bij elke toetsaanslag; oudere antwoorden worden genegeerd
        self.pending = False
        self.error = ""
        self.status = None
        self.table = None
        self.search = None
        self.extra_field = None
        self.segments = None
        self.on_choose = None  # without: Enter or double-click closes the window with the choice
        self.on_text = None  # after every keystroke in the search field
        self.on_extra = None  # after every keystroke in extra_field
        self.on_pick = None  # with it, a click or Tab fixes the selected suggestion (see choose)
        self.chosen = None  # the suggestion fixed by choose(); typing again clears it
        self.start_field = None  # optional 'Start' field; typing your own time sets start_edited
        self.start_edited = False
        self.tab_order = ()  # fields for Tab/Shift-Tab; empty = switch between the search field and extra_field
        return self

    @objc.python_method
    def apply_filter(self, text: str, keep: str | None = None) -> None:
        import re

        words = text.lower().split()
        self.items = [i for i in self.all_items if all(w in f"{i.label} {i.hint}".lower() for w in words)]
        typed_key = text.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", typed_key) and not any(i.key == typed_key for i in self.items):
            self.items.insert(0, PickItem(typed_key, f"{typed_key} · look up in Jira", "manual"))
        shown = {i.key for i in self.items}
        self.items += [i for i in self.remote_items if i.key not in shown]
        self.table.reloadData()
        if self.items:
            self.select_row(next((n for n, i in enumerate(self.items) if i.key == keep), 0))
        self.refresh_status()

    @objc.python_method
    def refresh_status(self) -> None:
        if self.pending:
            text = "Searching Jira…"
        elif self.error:
            text = f"Jira search failed: {self.error}"
        elif not self.items and self.search is not None and self.search.stringValue().strip():
            text = "No matches"
        else:
            text = ""
        if self.status is not None:
            self.status.setStringValue_(text)

    @objc.python_method
    def start_remote(self, text: str) -> None:
        """Na een korte pauze in het typen ook in Jira zoeken; een nieuwere toetsaanslag maakt dit antwoord ongeldig."""
        self.search_id += 1
        self.remote_items, self.error = [], ""
        text = text.strip()
        self.pending = self.remote_search is not None and len(text) >= REMOTE_MIN_CHARS
        if not self.pending:
            return
        search_id = self.search_id

        def work() -> None:
            time.sleep(REMOTE_DELAY)
            if search_id != self.search_id:
                return
            try:
                items, error = self.remote_search(text), ""
            except Exception as exc:  # een mislukte zoekopdracht mag de kiezer niet breken
                items, error = [], str(exc) or exc.__class__.__name__
            ui.on_main(self.show_remote, search_id, items, error)

        threading.Thread(target=work, daemon=True).start()

    @objc.python_method
    def show_remote(self, search_id: int, items: list, error: str) -> None:
        if search_id != self.search_id:
            return
        keep = self.selected().key if self.selected() else None
        self.pending, self.remote_items, self.error = False, items, error
        self.apply_filter(self.search.stringValue(), keep)
        if self.on_text:
            self.on_text()  # the list may need to grow now that the Jira results are in

    @objc.python_method
    def select_row(self, row: int) -> None:
        from AppKit import NSIndexSet

        if not self.items:
            return
        row = max(0, min(row, len(self.items) - 1))
        self.table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        self.table.scrollRowToVisible_(row)

    @objc.python_method
    def choose(self) -> PickItem | None:
        """Fix the selected suggestion: its name goes into the search field and later Jira answers can't change it."""
        item = self.chosen or self.selected()
        if item is None:
            return None
        self.chosen = item
        self.search_id += 1  # a Jira answer still on its way is ignored
        self.pending = False
        self.search.setStringValue_(item.key if item.hint == "manual" else item.label)  # no textDidChange
        self.refresh_status()
        if self.on_pick:
            self.on_pick()
        return item

    @objc.python_method
    def selected(self) -> PickItem | None:
        row = self.table.selectedRow()
        if 0 <= row < len(self.items):
            return self.items[row]
        return self.items[0] if self.items else None

    def numberOfRowsInTableView_(self, table):
        return len(self.items)

    def tableView_viewForTableColumn_row_(self, table, column, row):
        item = self.items[row]
        if column.identifier() == "hint":
            view = ui.label(SOURCE_NAMES.get(item.hint, item.hint), 11.5, color=ui.TERTIARY_TEXT)
            view.setAlignment_(2)
            return ui.centered(view)
        return ui.centered(ui.ticket_label(item.label, 13))

    def tableView_rowViewForRow_(self, table, row):
        return ui.SelectionRow.alloc().init()

    def controlTextDidChange_(self, notification):
        if notification is not None and self.start_field is not None and notification.object() == self.start_field:
            self.start_edited = True
            return
        if notification is not None and self.extra_field is not None and notification.object() == self.extra_field:
            if self.segments is not None and self.extra_field.stringValue().strip():
                self.segments.setSelectedSegment_(-1)  # een eigen tijd vervangt de knoppen
            if self.on_extra:
                self.on_extra()
            return
        self.chosen = None
        self.start_remote(self.search.stringValue())
        self.apply_filter(self.search.stringValue())
        if self.on_text:
            self.on_text()

    def segmentChanged_(self, sender):
        if self.extra_field is not None:
            self.extra_field.setStringValue_("")

    @objc.typedSelector(b"Z@:@@:")
    def control_textView_doCommandBySelector_(self, control, text_view, selector):
        name = _selector_name(selector)
        if name == "moveDown:":
            self.select_row(self.table.selectedRow() + 1)
            return True
        if name == "moveUp:":
            self.select_row(self.table.selectedRow() - 1)
            return True
        if name == "insertNewline:":
            if self.on_choose:
                self.on_choose()
            elif self.items:
                NSApp.stopModalWithCode_(0)
            else:
                NSBeep()  # niets om te kiezen: het venster blijft open
            return True
        if name == "insertTab:" and self.on_pick and control == self.search and self.search.stringValue().strip():
            self.choose()  # then Tab moves on as usual
        if name in ("insertTab:", "insertBacktab:") and self.tab_order:
            order = list(self.tab_order)
            step = 1 if name == "insertTab:" else -1
            index = order.index(control) if control in order else 0
            control.window().makeFirstResponder_(order[(index + step) % len(order)])
            return True
        if name in ("insertTab:", "insertBacktab:") and self.extra_field is not None:
            target = self.search if control == self.extra_field else self.extra_field
            control.window().makeFirstResponder_(target)
            return True
        return False

    def clicked_(self, sender):
        if self.on_pick and 0 <= self.table.clickedRow() < len(self.items):
            self.choose()
            if self.extra_field is not None:
                self.table.window().makeFirstResponder_(self.extra_field)  # next: how long

    def doubleClicked_(self, sender):
        if self.on_choose:
            self.on_choose()
        else:
            NSApp.stopModalWithCode_(0)

    def add_(self, sender):
        if self.on_choose:
            self.on_choose()


SOURCE_NAMES = {"recently used": "recent", "assigned": "assigned", "recent in Jira": "recent in Jira", "fixed": "fixed", "manual": "Jira"}


def pick(
    title: str,
    text: str,
    items: list[PickItem],
    preselect_key: str | None = None,
    buttons: tuple[str, ...] = ("Choose", "Cancel"),
    timeout: float | None = None,
    remote: Callable[[str], list[PickItem]] | None = None,
) -> tuple[PickItem | None, int]:
    """Zoekbare lijst. Typen filtert op key/titel, pijltjes bewegen, Enter kiest.
    Met `remote` wordt er na een korte pauze ook in Jira gezocht (op een achtergrondthread).

    Geeft (gekozen item, knopindex) terug; de laatste knop geldt als annuleren (item None).
    Na `timeout` seconden zonder antwoord: (None, TIMED_OUT).
    """
    item, button, _, _ = _pick(title, text, items, preselect_key, buttons, None, timeout=timeout, remote=remote)
    return item, button


def pick_with_duration(
    title: str, text: str, items: list[PickItem], duration: str, buttons: tuple[str, ...] = ("Add", "Cancel"),
    remote: Callable[[str], list[PickItem]] | None = None,
) -> tuple[PickItem | None, str]:
    """Ticketkiezer met een veld 'How long?' eronder (Tab vanuit het zoekveld). Geeft (item, duurtekst)."""
    item, _, text_value, _ = _pick(title, text, items, None, buttons, ("How long?", duration, "0:15", "e.g. 0:15, 20m or 1h"), remote=remote)
    return item, text_value


def pick_with_since(
    title: str, text: str, items: list[PickItem], preselect_key: str | None, choices: list[str],
    selected: int = 0, clock: str = "", buttons: tuple[str, ...] = ("Choose", "Cancel"),
    remote: Callable[[str], list[PickItem]] | None = None,
) -> tuple[PickItem | None, int | None, str]:
    """Ticketkiezer met 'Started' eronder: knoppen (bijv. Now / 5m ago / …) of een eigen tijd.

    Geeft (item, gekozen knop of None, tijdtekst) terug; een ingevulde tijd gaat voor de knoppen.
    """
    item, _, clock_text, segment = _pick(
        title, text, items, preselect_key, buttons, ("Started", clock, "hh:mm", ""), (choices, selected), remote=remote
    )
    return item, segment, clock_text


def _pick(title, text, items, preselect_key, buttons, field, segments=None, timeout=None, remote=None):
    """`field` = (label, waarde, placeholder, uitleg) voor een extra veld onder de lijst, of None.
    `segments` = (labels, gekozen) voor knoppen vóór dat veld. Geeft (item, knop, veldtekst, gekozen segment)."""
    card = ui.Card(title, text, width=600)
    width = card.inner
    controller = _PickerController.alloc().initWithItems_remote_(items, remote)

    search = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, width, 28))
    search.setControlSize_(NSControlSizeLarge)
    search.setFont_(ui.font(14))
    search.setPlaceholderString_("Search by key or title, or type a key like IN-1234")
    search.setDelegate_(controller)
    card.add(search, full_width=True, after=8)

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 260))
    scroll.setHasVerticalScroller_(True)
    scroll.setScrollerStyle_(1)  # NSScrollerStyleOverlay: de scrollbalk neemt geen kolombreedte in
    scroll.setBorderType_(NSNoBorder)
    scroll.setDrawsBackground_(False)
    table = NSTableView.alloc().initWithFrame_(scroll.bounds())
    for identifier, column_width in (("label", width - 170), ("hint", 130)):  # rechts ruimte voor de scrollbalk
        column = NSTableColumn.alloc().initWithIdentifier_(identifier)
        column.setWidth_(column_width)
        column.setEditable_(False)
        table.addTableColumn_(column)
    table.setHeaderView_(None)
    table.setRowHeight_(28)
    table.setIntercellSpacing_((10, 2))
    table.setBackgroundColor_(NSColor.clearColor())
    table.setFocusRingType_(1)  # geen focusrand
    table.setDataSource_(controller)
    table.setDelegate_(controller)
    table.setTarget_(controller)
    table.setDoubleAction_("doubleClicked:")
    scroll.setDocumentView_(table)
    card.add(ui.fixed(scroll, width, 260), after=4)
    status = ui.label("", 12, color=ui.TERTIARY_TEXT)
    card.add(status, full_width=True)
    controller.table, controller.search, controller.status = table, search, status

    extra_field = None
    segmented = None
    if field is not None and segments is not None:
        field_label, value, placeholder, _ = field
        labels, selected = segments
        segmented = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            labels, NSSegmentSwitchTrackingSelectOne, controller, "segmentChanged:"
        )
        segmented.setSelectedSegment_(-1 if value else selected)
        extra_field = _field(value, NSMakeRect(0, 0, 64, 24), placeholder)
        ui.constrain(extra_field, width=64)
        card.add(ui.row(
            ui.label(field_label, 12.5, ui.MEDIUM, ui.SECONDARY_TEXT), segmented,
            ui.label("or at", 12.5, color=ui.SECONDARY_TEXT), extra_field, spacing=8,
        ))
        extra_field.setDelegate_(controller)
        controller.extra_field, controller.segments = extra_field, segmented
    elif field is not None:
        field_label, value, placeholder, hint = field
        extra_field = _field(value, NSMakeRect(0, 0, 70, 24), placeholder)
        ui.constrain(extra_field, width=70)
        card.add(ui.row(
            ui.label(field_label, 12.5, ui.MEDIUM, ui.SECONDARY_TEXT), extra_field,
            ui.label(hint, 12, color=ui.TERTIARY_TEXT), spacing=8,
        ))
        extra_field.setDelegate_(controller)
        controller.extra_field = extra_field

    _button_row(card, list(buttons), cancel=len(buttons) - 1)
    card.first_responder = search

    table.reloadData()
    preselect = next((n for n, i in enumerate(items) if i.key == preselect_key), 0)
    controller.select_row(preselect)

    button = card.run(timeout)
    extra_text = extra_field.stringValue() if extra_field is not None else ""
    segment = segmented.selectedSegment() if segmented is not None else -1
    segment = None if segment < 0 else segment
    if button == len(buttons) - 1 or button == TIMED_OUT:
        return None, button, extra_text, segment
    return controller.selected(), button, extra_text, segment


# -- formulieren --------------------------------------------------------------


class _FlippedView(NSView):
    def isFlipped(self):
        return True


def _label(text: str, frame, bold: bool = False, align_right: bool = False) -> NSTextField:
    label = NSTextField.labelWithString_(text)
    label.setFrame_(frame)
    if bold:
        label.setFont_(NSFont.boldSystemFontOfSize_(NSFont.systemFontSize()))
    if align_right:
        label.setAlignment_(2)  # NSTextAlignmentRight
    label.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
    return label


def _field(value: str, frame, placeholder: str = "", secure: bool = False) -> NSTextField:
    cls = NSSecureTextField if secure else NSTextField
    field = cls.alloc().initWithFrame_(frame)
    field.setStringValue_(value)
    field.setPlaceholderString_(placeholder)
    return field


@dataclass
class FormField:
    name: str
    label: str
    value: str = ""
    placeholder: str = ""
    secure: bool = False
    section: str = ""  # kopje boven dit veld (alleen bij het eerste veld van een blok)
    choices: tuple[tuple[str, str], ...] = ()  # (value, label) pairs: a switch instead of a text field
    lines: int = 1  # more than 1: a taller field whose text wraps onto the next line


def form(title: str, text: str, fields: list[FormField], buttons: tuple[str, ...] = ("Save", "Cancel")) -> dict | None:
    """Label/invoer-paren onder elkaar, in blokken met een kopje. None bij de laatste knop (annuleren)."""
    card = ui.Card(title, width=620)
    label_width, field_width = 170, card.inner - 170 - 12
    inputs, choices = {}, {}
    for item in fields:
        if item.section:
            card.add(ui.label(item.section, 13, ui.SEMIBOLD), after=6)
            if len(inputs):
                card.stack.setCustomSpacing_afterView_(18, card.stack.arrangedSubviews()[-2])
        caption = ui.label(item.label, 13, color=ui.SECONDARY_TEXT)
        caption.setAlignment_(2)
        ui.constrain(caption, width=label_width)
        if item.choices:
            names = [name for name, _ in item.choices]
            field = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
                [label for _, label in item.choices], NSSegmentSwitchTrackingSelectOne, None, None
            )
            field.setSelectedSegment_(names.index(item.value) if item.value in names else 0)
            field.setIdentifier_(item.name)
            choices[item.name] = names
        else:
            height = 24 if item.lines <= 1 else item.lines * 17 + 7
            field = _field(item.value, NSMakeRect(0, 0, field_width, height), item.placeholder, item.secure)
            if item.lines > 1:
                field.setUsesSingleLineMode_(False)
                field.cell().setWraps_(True)
                field.cell().setScrollable_(False)
                ui.constrain(field, width=field_width, height=height)
            else:
                ui.constrain(field, width=field_width)
        card.add(ui.row(caption, field, spacing=12), after=6)
        inputs[item.name] = field
    for current, following in zip(list(inputs.values()), list(inputs.values())[1:]):
        current.setNextKeyView_(following)
    card.text(text, 11.5, ui.TERTIARY_TEXT)
    _button_row(card, list(buttons), cancel=len(buttons) - 1)
    card.first_responder = next(iter(inputs.values()), None)
    button = card.run()
    if button == len(buttons) - 1 or button == TIMED_OUT:
        return None
    return {
        name: choices[name][field.selectedSegment()] if name in choices else field.stringValue()
        for name, field in inputs.items()
    }


# -- dagoverzicht -------------------------------------------------------------


@dataclass
class OverviewLine:
    issue_key: str  # lokale sleutel, leeg voor een nieuwe regel
    label: str
    tracked: str
    target_key: str
    target_key_editable: bool
    duration: str
    description: str
    description_placeholder: str = ""
    removed: bool = False
    auto_duration: str = ""  # getrackte tijd als jij de tijd hebt aangepast; leeg = niet aangepast
    periods: tuple = ()  # gaps you filled with this line: ((start, end), ...); the app stores them as tracked time


class _RowRemover(NSObject):
    """✕ bij een regel: dimt de regel en markeert hem als verwijderd; nog een klik zet hem terug."""

    def initWithViews_(self, views):
        self = objc.super(_RowRemover, self).init()
        if self is None:
            return None
        self.views = views
        self.removed = False
        self.on_change = None
        return self

    def toggle_(self, button):
        self.set_removed(not self.removed, button)
        if self.on_change:
            self.on_change()

    @objc.python_method
    def set_removed(self, removed: bool, button) -> None:
        self.removed = removed
        for view in self.views:
            view.setAlphaValue_(0.3 if removed else 1.0)
            if isinstance(view, NSTextField) and view.isEditable():
                view.setEnabled_(not removed)
        button.setImage_(ui.symbol_image("arrow.uturn.backward.circle" if removed else "xmark.circle", 14))
        button.setToolTip_("Restore" if removed else "Remove line")


class _ResetButton(NSObject):
    """✎ naast een aangepaste tijd: klik = terug naar de getrackte tijd."""

    def initWithField_value_(self, field, value):
        self = objc.super(_ResetButton, self).init()
        if self is None:
            return None
        self.field, self.value, self.on_change = field, value, None
        return self

    def reset_(self, button):
        self.field.setStringValue_(self.value)
        self.field.setTextColor_(ui.PRIMARY_TEXT)
        button.setHidden_(True)
        if self.on_change:
            self.on_change()


class _TotalsUpdater(NSObject):
    """Werkt de totalen bovenaan het dagoverzicht bij zodra je een tijd aanpast."""

    def initWithRefresh_(self, refresh):
        self = objc.super(_TotalsUpdater, self).init()
        if self is None:
            return None
        self.refresh = refresh
        return self

    def controlTextDidChange_(self, _notification):
        self.refresh()


def _icon_button(symbol_name: str, tooltip: str, target, action: str, color=None) -> NSButton:
    view = NSButton.buttonWithImage_target_action_(ui.symbol_image(symbol_name, 14), target, action)
    view.setBordered_(False)
    view.setContentTintColor_(color or ui.TERTIARY_TEXT)
    view.setToolTip_(tooltip)
    return view


def _separator(width: float) -> NSBox:
    line = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, width, 1))
    line.setBoxType_(2)  # NSBoxSeparator
    return line


SEND, SAVE, CANCEL = 0, 1, 2
ROW_HEIGHT, HEADER_HEIGHT, LIST_MAX_HEIGHT = 40, 24, 400
SUGGESTION_ROWS = 5


def overview(
    title: str,
    text: str,
    lines: list[OverviewLine],
    totals: Callable[[list[tuple[str, bool]]], tuple[str, int | None, int, int]] | None = None,
    chips: list[str] = (),
    day: str = "",
    blocks: list[timeline.Block] = (),
    pick_ticket: Callable[[str], str | None] | None = None,
    items: list[PickItem] = (),
    remote: Callable[[str], list[PickItem]] | None = None,
    new_line: Callable[[PickItem, str], OverviewLine] | None = None,
    view: str = "list",
    on_view: Callable[[str], None] | None = None,
    jira_site: str = "",
) -> tuple[str | None, list[OverviewLine]]:
    """Editable table per ticket. Returns ('send' | 'save' | None, edited lines).

    `totals` gets (duration, removed) per line and returns (text, in Tempo, to send, target) in seconds;
    `chips` are the worklogs already in Tempo. With `day` and `blocks` you can switch at the top to a
    calendar of that day. `pick_ticket(label)` adds a magnifier next to the key field of a fixed option.

    With `new_line` there is a search field at the top (over `items`, and via `remote` in Jira) to add time:
    for a ticket that is already in the list the time is added, otherwise `new_line(item, duration)` makes a line.
    Filling a gap in the calendar puts that period in the line's `periods`; the app stores it.

    `view` is the view the window opens in ('list' or 'calendar'); `on_view(name)` hears every switch.

    With `jira_site`, every ticket in both views can be opened in Jira in the browser.
    """
    card = ui.Card(title, text, width=800)
    lines = list(lines)
    width = card.inner - 18  # room for the scroll bar; lines can be added
    columns = {"ticket": 280, "tracked": 60, "duration": 64, "reset": 22, "description": width - 280 - 60 - 64 - 22 - 26 - 5 * 10, "remove": 26}
    x_positions, x = {}, 0
    for name, column_width in columns.items():
        x_positions[name] = x
        x += column_width + 10

    # Totalen: tekst, status en een balk (paars = al in Tempo, blauw = te versturen).
    totals_label = ui.label("", 13, ui.MEDIUM)
    status = ui.label("", 12.5, ui.MEDIUM)
    progress = ui.bar(8)
    legend_tempo = ui.label("", 12, color=ui.SECONDARY_TEXT)
    legend_send = ui.label("", 12, color=ui.SECONDARY_TEXT)
    if totals:
        card.add(ui.row(totals_label, ui.spacer(), status), full_width=True, after=8)
        card.add(progress, full_width=True, after=6)
        card.add(ui.row(_dot(ui.TEMPO), legend_tempo, ui.label("", 4), _dot(ui.ACCENT_FILL), legend_send, spacing=6), after=14)
    if chips:
        for start in range(0, len(chips), 3):
            card.add(ui.row(*[ui.chip(c, ui.TEMPO, ui.TEMPO_TINT) for c in chips[start:start + 3]], spacing=8), after=6)
        card.stack.setCustomSpacing_afterView_(14, card.stack.arrangedSubviews()[-1])

    # Add time: a search field with suggestions below it, a duration and Add.
    adder = None
    if new_line is not None:
        adder = _PickerController.alloc().initWithItems_remote_(items, remote)
        search = NSSearchField.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 28))
        search.setControlSize_(NSControlSizeLarge)
        search.setFont_(ui.font(14))
        search.setPlaceholderString_("Add time: search for a ticket by key or title")
        search.setIdentifier_("quick-add")
        search.setDelegate_(adder)
        add_duration = _field("", NSMakeRect(0, 0, 70, 28), "0:30")
        add_duration.setAlignment_(2)
        add_duration.setFont_(ui.digits(14, ui.MEDIUM))
        ui.constrain(add_duration, width=70, height=28)
        add_duration.setDelegate_(adder)
        add_start = None
        if day:
            add_start = _field("", NSMakeRect(0, 0, 70, 28), "hh:mm")
            add_start.setAlignment_(2)
            add_start.setFont_(ui.digits(14, ui.MEDIUM))
            add_start.setToolTip_("Start time. Filled with the first free time; change it if you like.")
            ui.constrain(add_start, width=70, height=28)
            add_start.setDelegate_(adder)
        add_button = NSButton.buttonWithTitle_target_action_("Add", adder, "add:")
        add_button.setControlSize_(NSControlSizeLarge)
        start_label = ui.label("at", 13, color=ui.SECONDARY_TEXT) if add_start is not None else None
        card.add(ui.row(search, start_label, add_start, add_duration, add_button, spacing=8), full_width=True, after=4)

        suggestions = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, card.inner, SUGGESTION_ROWS * 30))
        suggestions.setHasVerticalScroller_(True)
        suggestions.setScrollerStyle_(1)
        suggestions.setBorderType_(NSNoBorder)
        suggestions.setDrawsBackground_(False)
        table = NSTableView.alloc().initWithFrame_(suggestions.bounds())
        for identifier, column_width in (("label", card.inner - 170), ("hint", 130)):
            column = NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.setWidth_(column_width)
            column.setEditable_(False)
            table.addTableColumn_(column)
        table.setHeaderView_(None)
        table.setRowHeight_(28)
        table.setIntercellSpacing_((10, 2))
        table.setBackgroundColor_(NSColor.clearColor())
        table.setFocusRingType_(1)
        table.setDataSource_(adder)
        table.setDelegate_(adder)
        table.setTarget_(adder)
        table.setAction_("clicked:")  # one click fixes the ticket (see _PickerController.choose)
        table.setDoubleAction_("doubleClicked:")
        suggestions.setDocumentView_(table)
        suggestions_view = card.add(ui.fixed(suggestions, card.inner, SUGGESTION_ROWS * 30))
        suggestions_view.setHidden_(True)
        note = ui.label("", 12, color=ui.SECONDARY_TEXT)
        search_status = ui.label("", 12, color=ui.TERTIARY_TEXT)
        card.add(ui.row(note, ui.spacer(), search_status), full_width=True, after=12)
        adder.table, adder.search, adder.status, adder.extra_field = table, search, search_status, add_duration
        adder.start_field = add_start
        adder.tab_order = tuple(f for f in (search, add_start, add_duration) if f is not None)
        adder.apply_filter("")

    switcher = None
    if day:
        switcher = _ViewSwitcher.alloc().initWithCard_(card)
        switcher.on_view = on_view
        segmented = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ["List", "Calendar"], NSSegmentSwitchTrackingSelectOne, switcher, "switch:"
        )
        segmented.setSelectedSegment_(0)
        segmented.setIdentifier_("view-switch")  # so the screenshots can find it
        ui.constrain(segmented, width=200)
        card.add(segmented, after=10)

    # Table: rebuilt when a line is added.
    document = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, HEADER_HEIGHT))
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, card.inner, LIST_MAX_HEIGHT))
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(NSNoBorder)
    scroll.setDocumentView_(document)
    list_view = card.add(ui.fixed(scroll, card.inner, LIST_MAX_HEIGHT))
    state = {"widgets": [], "pickers": [], "updater": None}

    def build_rows() -> None:
        for view in list(document.subviews()):
            view.removeFromSuperview()
        content_height = HEADER_HEIGHT + ROW_HEIGHT * len(lines)
        document.setFrameSize_((width, content_height))
        for name, header in (("ticket", "Ticket"), ("tracked", "Tracked"), ("duration", "Log"), ("description", "Description")):
            heading = ui.label(header, 11.5, ui.MEDIUM, ui.TERTIARY_TEXT)
            heading.setFrame_(NSMakeRect(x_positions[name], 2, columns[name], 16))
            if name in ("tracked", "duration"):
                heading.setAlignment_(2)
            document.addSubview_(heading)
        top_line = _separator(width)
        top_line.setFrameOrigin_((0, HEADER_HEIGHT - 2))
        document.addSubview_(top_line)

        widgets, pickers = [], []
        for n, line in enumerate(lines):
            widgets.append(_overview_row(document, line, n, n == len(lines) - 1, width, columns, x_positions, pick_ticket, pickers, jira_site))
        state["widgets"], state["pickers"] = widgets, pickers
        scroll.setHasVerticalScroller_(content_height > LIST_MAX_HEIGHT)
        _set_height(list_view, min(content_height, LIST_MAX_HEIGHT))
        document.scrollPoint_((0, 0))
        if state["updater"] is not None:
            for _, _, duration, _, remover, resetter in widgets:
                duration.setDelegate_(state["updater"])
                remover.on_change = refresh
                if resetter:
                    resetter.on_change = refresh
            refresh()

    def collect() -> list[OverviewLine]:
        edited = []
        for line, key_field, duration, description, remover, _ in state["widgets"]:
            edited.append(
                replace(
                    line,
                    target_key=key_field.stringValue().strip().upper() if key_field else line.target_key,
                    duration=duration.stringValue(),
                    description=description.stringValue().strip(),
                    removed=remover.removed,
                )
            )
        return edited

    def refresh() -> None:
        text_value, in_tempo, to_send, target = totals([(w[2].stringValue(), w[4].removed) for w in state["widgets"]])
        summary, _, verdict = text_value.partition("  ·  ")
        totals_label.setStringValue_(summary)
        status.setStringValue_(verdict)
        status.setTextColor_(ui.SUCCESS if "complete" in verdict else ui.WARNING)
        total = (in_tempo or 0) + to_send
        scale = max(target or 0, total, 1)
        progress.set_segments([((in_tempo or 0) / scale, ui.TEMPO), (to_send / scale, ui.ACCENT_FILL)])
        legend_tempo.setStringValue_(f"In Tempo {_hm(in_tempo or 0)}" if in_tempo is not None else "In Tempo: couldn't read")
        legend_send.setStringValue_(f"To send {_hm(to_send)}")

    if totals:
        state["updater"] = _TotalsUpdater.alloc().initWithRefresh_(refresh)
    build_rows()

    calendar = None
    if switcher is not None:
        calendar_view, calendar = _calendar(day, list(blocks), card.inner, jira_site)
        calendar_view.setHidden_(True)
        card.add(calendar_view)
        switcher.views = (list_view, calendar_view)
        if view == "calendar":
            segmented.setSelectedSegment_(1)
            switcher.show(True)

    if adder is not None:
        def show_suggestions() -> None:
            """Suggestions only while searching, as tall as needed (at most SUGGESTION_ROWS)."""
            rows = min(len(adder.items), SUGGESTION_ROWS) if search.stringValue().strip() and adder.chosen is None else 0
            suggestions_view.setHidden_(rows == 0)
            _set_height(suggestions_view, max(rows, 1) * 30)
            _fit(card)

        def known_blocks() -> list[timeline.Block]:
            return calendar.view.blocks if calendar is not None else list(blocks)

        def entered_seconds(default: int = 0) -> int:
            try:
                return parse_duration(add_duration.stringValue()) or default
            except ValueError:
                return default

        def suggest_start() -> None:
            """As long as you don't fill in Start yourself: the first free time this duration fits in."""
            if add_start is None or adder.start_edited:
                return
            add_start.setStringValue_(_clock(timeline.free_start(day, known_blocks(), entered_seconds(30 * 60))))

        def say(text: str, warning: bool = False) -> None:
            note.setStringValue_(text)
            note.setTextColor_(ui.WARNING if warning else ui.SECONDARY_TEXT)

        def fill_gap(start: float, end: float) -> None:
            add_duration.setStringValue_(_hm(int(end - start)))
            if add_start is not None:
                add_start.setStringValue_(_clock(start))
                adder.start_edited = True  # a shorter duration keeps it at the start of the gap
            say(f"For {_clock(start)}–{_clock(end)}, not tracked yet. Pick a ticket and press Enter.")
            card.panel.makeFirstResponder_(search)

        def chosen_period(seconds: int) -> tuple[float, float] | None:
            """(start, end) from the Start field, or None with a note if that isn't possible."""
            if add_start is None:
                return None
            start = _parse_clock(day, add_start.stringValue())
            if start is None:
                say("Enter a start time like 9:30 or 14:00.", warning=True)
                return None
            end = start + seconds
            if _clock_day(end - 1) != day:
                say("That runs past midnight. Pick an earlier start or a shorter time.", warning=True)
                return None
            clash = timeline.overlaps(known_blocks(), start, end)
            if clash:
                other = clash[0]
                say(
                    f"{_clock(start)}–{_clock(end)} overlaps {_block_title(other)} ({_clock(other.start)}–{_clock(other.end)}). "
                    "Pick a free time, or change that line first.",
                    warning=True,
                )
                return None
            return start, end

        def add_time() -> None:
            item = adder.chosen or (adder.selected() if search.stringValue().strip() else None)
            if item is None:
                NSBeep()
                card.panel.makeFirstResponder_(search)
                return
            text_value = add_duration.stringValue().strip()
            if not text_value:
                adder.choose()
                card.panel.makeFirstResponder_(add_duration)  # a duration first
                return
            try:
                seconds = parse_duration(text_value)
            except ValueError:
                seconds = 0
            if seconds <= 0:
                NSBeep()
                add_duration.setTextColor_(ui.WARNING)
                card.panel.makeFirstResponder_(add_duration)
                return
            period = chosen_period(seconds)
            if add_start is not None and period is None:
                NSBeep()
                card.panel.makeFirstResponder_(add_start)
                return
            current = collect()
            existing = next((n for n, l in enumerate(current) if l.issue_key == item.key and not l.removed), None)
            if existing is None:
                line = new_line(item, _hm(seconds))
                current.append(replace(line, periods=(period,) if period else ()))
            else:
                line = current[existing]
                try:
                    before = parse_duration(line.duration)
                except ValueError:
                    before = 0
                current[existing] = replace(line, duration=_hm(before + seconds), periods=line.periods + ((period,) if period else ()))
            lines[:] = current
            build_rows()
            if period and calendar is not None:
                calendar.add_block(
                    timeline.Block(period[0], period[1], item.key, item.label, timeline.TRACKED, pending=True, jira_key=line.target_key)
                )
            say("")
            adder.chosen = None
            search.setStringValue_("")
            add_duration.setStringValue_("")
            add_duration.setTextColor_(ui.PRIMARY_TEXT)
            adder.start_edited = False
            suggest_start()  # the next item comes after this one
            adder.apply_filter("")
            show_suggestions()
            _fit(card)
            card.panel.makeFirstResponder_(search)

        adder.on_choose, adder.on_text, adder.on_extra, adder.on_pick = add_time, show_suggestions, suggest_start, show_suggestions
        if calendar is not None:
            calendar.on_gap = fill_gap
        suggest_start()

    card.buttons(
        [ui.button("Cancel", CANCEL), ui.button("Save", SAVE), ui.button("Send to Tempo", SEND, primary=True)],
        cancel_code=CANCEL,
    )
    if adder is not None:
        card.first_responder = search
    elif state["widgets"]:
        card.first_responder = state["widgets"][0][2]
    button = card.run()
    edited = collect()
    return {SEND: "send", SAVE: "save"}.get(button), edited


def _overview_row(document, line, n, last, width, columns, x_positions, pick_ticket, pickers, jira_site=""):
    """One line of the day overview. Returns (line, key field, duration, description, remover, resetter)."""
    y = HEADER_HEIGHT + n * ROW_HEIGHT
    middle = y + (ROW_HEIGHT - 24) / 2
    ticket_x, ticket_w = x_positions["ticket"], columns["ticket"]
    row_views = []
    key_field = None
    # Open the ticket in Jira: always for a key you type (it can change), otherwise only for a real ticket.
    can_open = bool(jira_site) and (line.target_key_editable or issue_url(jira_site, line.target_key) is not None)
    open_x = ticket_x + ticket_w - _OPEN_WIDTH + 4
    if line.target_key_editable:
        if can_open:
            ticket_w -= _OPEN_WIDTH
        search_width = 26 if pick_ticket else 0  # magnifier to the right of the key field
        key_width = 120
        if line.label:
            name_label = ui.label(f"{line.label}  →", 13)
            name_label.setFrame_(NSMakeRect(ticket_x, middle + 3, ticket_w - key_width - search_width - 8, 18))
            document.addSubview_(name_label)
            row_views.append(name_label)
        else:
            key_width = ticket_w - search_width
        key_field = _field(
            line.target_key, NSMakeRect(ticket_x + ticket_w - search_width - key_width, middle, key_width, 24),
            "IN-100" if line.label else "Jira key, like IN-100",
        )
        document.addSubview_(key_field)
        row_views.append(key_field)
        if pick_ticket:
            picker = _TicketPicker.alloc().initWithField_label_pick_(key_field, line.label, pick_ticket)
            pickers.append(picker)  # NSButton does not retain its target
            search = _icon_button("magnifyingglass", "Search for a ticket", picker, "pick:")
            search.setFrame_(NSMakeRect(ticket_x + ticket_w - 22, middle + 2, 22, 20))
            document.addSubview_(search)
            row_views.append(search)
    else:
        # Name first, then the key, then ↗; a long name is cut off so the key stays visible.
        name, key = _name_and_key(line.label, line.target_key)
        name_text = ui.name_text(name, 13)
        key_text = ui.key_text(key, 13) if key else None
        name_width, key_x = _name_then_key(name_text, key_text, ticket_w - (_OPEN_WIDTH if can_open else 0))
        name_label = ui.label("", 13)
        name_label.setAttributedStringValue_(name_text)
        name_label.setFrame_(NSMakeRect(ticket_x, middle + 3, name_width, 18))
        name_label.setToolTip_(line.label)
        document.addSubview_(name_label)
        row_views.append(name_label)
        if key_text is not None:
            key_label = ui.label("", 13)
            key_label.setAttributedStringValue_(key_text)
            key_label.setFrame_(NSMakeRect(ticket_x + key_x, middle + 3, key_text.size().width + 4, 18))
            document.addSubview_(key_label)
            row_views.append(key_label)
            open_x = ticket_x + key_x + key_text.size().width + 4
    if can_open:
        opener = _IssueOpener.alloc().initWithSite_key_(
            jira_site, (lambda: key_field.stringValue()) if key_field is not None else (lambda: line.target_key)
        )
        pickers.append(opener)  # NSButton does not retain its target
        tooltip = "Open the ticket in Jira" if key_field is not None else f"Open {line.target_key} in Jira"
        open_button = _icon_button(ui.LINK_SYMBOL, tooltip, opener, "open:", ui.ACCENT)
        open_button.setFrame_(NSMakeRect(open_x, middle + 2, 20, 20))
        document.addSubview_(open_button)
        row_views.append(open_button)
    tracked = ui.label(line.tracked, fnt=ui.digits(13), color=ui.SECONDARY_TEXT)
    tracked.setFrame_(NSMakeRect(x_positions["tracked"], middle + 3, columns["tracked"], 18))
    tracked.setAlignment_(2)
    document.addSubview_(tracked)
    duration = _field(line.duration, NSMakeRect(x_positions["duration"], middle, columns["duration"], 24), "0:00")
    duration.setAlignment_(2)
    duration.setFont_(ui.digits(13, ui.MEDIUM))
    description = _field(
        line.description, NSMakeRect(x_positions["description"], middle, columns["description"], 24), line.description_placeholder
    )
    description.setToolTip_(line.description_placeholder)
    document.addSubview_(duration)
    document.addSubview_(description)
    row_views += [tracked, duration, description]

    resetter = None
    if line.auto_duration and line.duration != line.auto_duration:
        duration.setTextColor_(ui.WARNING)
        resetter = _ResetButton.alloc().initWithField_value_(duration, line.auto_duration)
        reset = _icon_button(
            "pencil.circle.fill", f"You changed this time. Click to use the tracked time ({line.auto_duration}).",
            resetter, "reset:", ui.WARNING,
        )
        reset.setFrame_(NSMakeRect(x_positions["reset"], middle + 2, columns["reset"], 20))
        document.addSubview_(reset)
        row_views.append(reset)

    remover = _RowRemover.alloc().initWithViews_(row_views)
    remove = _icon_button("xmark.circle", "Remove line", remover, "toggle:")
    remove.setFrame_(NSMakeRect(x_positions["remove"], middle + 2, columns["remove"], 20))
    remover.set_removed(line.removed, remove)
    document.addSubview_(remove)
    if not last:
        line_view = _separator(width)
        line_view.setFrameOrigin_((0, y + ROW_HEIGHT - 1))
        document.addSubview_(line_view)
    return line, key_field, duration, description, remover, resetter


def _set_height(view, height: float) -> None:
    """Changes the fixed height that ui.fixed() set."""
    for constraint in view.constraints():
        if constraint.firstAttribute() == 8 and constraint.secondItem() is None:  # NSLayoutAttributeHeight
            constraint.setConstant_(height)


def _fit(card) -> None:
    """Fit the window to its content; the top edge stays put."""
    panel = card.panel
    frame = panel.frame()
    top = frame.origin.y + frame.size.height
    panel.layoutIfNeeded()
    panel.setContentSize_(card.stack.fittingSize())
    panel.setFrameTopLeftPoint_((frame.origin.x, top))


def _clock(timestamp: float) -> str:
    return time.strftime("%H:%M", time.localtime(timestamp))


def _clock_day(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def _parse_clock(day: str, text: str) -> float | None:
    """'9:30', '09.30', '930' or '14' on `day` -> timestamp; None if it isn't a time."""
    import re
    from datetime import datetime

    match = re.fullmatch(r"(\d{1,2})(?:[:.]?(\d{2}))?", text.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2) or 0)
    if hours > 23 or minutes > 59:
        return None
    return datetime.strptime(f"{day} {hours}:{minutes}", "%Y-%m-%d %H:%M").timestamp()  # also correct on a day with a DST change


class _TicketPicker(NSObject):
    """Magnifier next to a key field: pick a ticket by searching, its key goes into the field."""

    def initWithField_label_pick_(self, field, label, pick):
        self = objc.super(_TicketPicker, self).init()
        if self is None:
            return None
        self.field, self.label, self.pick = field, label, pick
        return self

    def pick_(self, _button):
        key = self.pick(self.label)
        if key:
            self.field.setStringValue_(key)
        self.field.window().makeFirstResponder_(self.field)


_OPEN_WIDTH = 26  # the "open in Jira" button at the right of the ticket column


def open_issue(site: str, key: str) -> bool:
    """Opens the ticket in the default browser. False (and nothing opens) without a valid key."""
    url = issue_url(site, key)
    if url is None:
        return False
    from AppKit import NSWorkspace
    from Foundation import NSURL

    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
    return True


class _IssueOpener(NSObject):
    """Button next to a ticket: opens it in Jira. `key()` gives the key at the moment you click."""

    def initWithSite_key_(self, site, key):
        self = objc.super(_IssueOpener, self).init()
        if self is None:
            return None
        self.site, self.key = site, key
        return self

    def open_(self, _button):
        open_issue(self.site, self.key())


class _ViewSwitcher(NSObject):
    """List | Calendar: shows one of the two, fits the window height and reports the choice."""

    def initWithCard_(self, card):
        self = objc.super(_ViewSwitcher, self).init()
        if self is None:
            return None
        self.card, self.views, self.on_view = card, (), None
        return self

    @objc.python_method
    def show(self, calendar: bool) -> None:
        list_view, calendar_view = self.views
        list_view.setHidden_(calendar)
        calendar_view.setHidden_(not calendar)

    def switch_(self, control):
        calendar = control.selectedSegment() == 1
        self.show(calendar)
        _fit(self.card)
        if self.on_view:
            self.on_view("calendar" if calendar else "list")


CALENDAR_MAX_HEIGHT = 420
_TIME_WIDTH = 44  # start time to the left of the line
_RAIL_X = 60  # the vertical line
_CARD_X = 78
_BLOCK_HEIGHT, _GAP_HEIGHT, _SPACING = 50, 34, 8
_NOW_HEIGHT = 22


class _Calendar:
    """The Calendar view in the day overview: updating blocks, and a click on a gap."""

    def __init__(self, view, scroll, wrapper):
        self.view, self.scroll, self.wrapper, self.on_gap = view, scroll, wrapper, None
        view.owner = self

    def add_block(self, block: timeline.Block) -> None:
        self.view.set_blocks(self.view.blocks + [block])
        self.fit()

    def fit(self) -> None:
        height = self.view.frame().size.height
        self.scroll.setHasVerticalScroller_(height > CALENDAR_MAX_HEIGHT)
        _set_height(self.wrapper, min(height, CALENDAR_MAX_HEIGHT))


def _calendar(day: str, blocks: list[timeline.Block], width: float, jira_site: str = ""):
    """Your day as a timeline: one line with a dot per block or gap. Returns (view for the card, _Calendar)."""
    view = _CalendarView.alloc().initWithFrame_(NSMakeRect(0, 0, width - 18, 100))
    view.day, view.site = day, jira_site
    view.set_blocks(blocks)
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, CALENDAR_MAX_HEIGHT))
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(NSNoBorder)
    scroll.setDocumentView_(view)
    wrapper = ui.fixed(scroll, width, CALENDAR_MAX_HEIGHT)
    calendar = _Calendar(view, scroll, wrapper)
    calendar.fit()
    return wrapper, calendar


class _CalendarView(NSView):
    def isFlipped(self):
        return True

    @objc.python_method
    def set_blocks(self, blocks: list[timeline.Block]) -> None:
        """Lay out the rows again: (start, end, block or None for a gap, y, height)."""
        self.blocks = list(blocks)
        self.rows, y = [], 4
        for start, end, block in timeline.entries(self.blocks):
            height = _BLOCK_HEIGHT if block else _GAP_HEIGHT
            self.rows.append((start, end, block, y, height))
            y += height + _SPACING
        now = time.time()
        self.now = None
        if self.blocks and time.strftime("%Y-%m-%d", time.localtime(now)) == self.day:
            self.now = (now, y)
            y += _NOW_HEIGHT
        self.setFrameSize_((self.frame().size.width, max(y, 40)))
        self.removeAllToolTips()
        for row in self.rows:
            self.addToolTipRect_owner_userData_(self.card_rect(row), self, None)
        if self.window():
            self.window().invalidateCursorRectsForView_(self)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def card_rect(self, row):
        _, _, _, y, height = row
        return NSMakeRect(_CARD_X, y, self.bounds().size.width - _CARD_X - 4, height)

    def view_stringForToolTip_point_userData_(self, view, tag, point, data):
        for row in self.rows:
            start, end, block, _, _ = row
            if _inside(point, self.card_rect(row)):
                if block is None:
                    return f"{_clock(start)}–{_clock(end)}: nothing tracked. Click to fill it with a ticket."
                state = "in Tempo" if block.lane == timeline.TEMPO else ("added, not saved yet" if block.pending else "to send")
                hint = f"\nClick to open {block.link_key} in Jira" if self.can_open(block) else ""
                return f"{_block_title(block)}\n{_block_period(block)} · {state}{hint}"
        return ""

    @objc.python_method
    def can_open(self, block: timeline.Block) -> bool:
        return issue_url(getattr(self, "site", ""), block.link_key) is not None

    def resetCursorRects(self):
        from AppKit import NSCursor

        for row in self.rows:
            block = row[2]
            if block is None or self.can_open(block):
                self.addCursorRect_cursor_(self.card_rect(row), NSCursor.pointingHandCursor())

    def mouseDown_(self, event):
        self.click_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    @objc.python_method
    def click_at(self, point) -> None:
        """A gap: fill it with a ticket. A ticket: open it in Jira."""
        for row in self.rows:
            start, end, block, _, _ = row
            if not _inside(point, self.card_rect(row)):
                continue
            if block is None:
                if self.owner.on_gap:
                    self.owner.on_gap(start, end)
            elif self.can_open(block):
                open_issue(self.site, block.link_key)
            return

    def drawRect_(self, rect):
        if not self.rows:
            _draw("Nothing tracked this day.", ui.font(13), ui.SECONDARY_TEXT, NSMakeRect(_CARD_X, 8, 300, 18))
            return
        # The line runs from the first to the last dot.
        first_y = self.rows[0][3] + 14
        last_y = (self.now[1] + 9) if self.now else self.rows[-1][3] + 14
        NSColor.separatorColor().set()
        NSBezierPath.fillRect_(NSMakeRect(_RAIL_X - 1, first_y, 2, last_y - first_y))
        for row in self.rows:
            if row[2] is None:
                self.draw_gap(row)
            else:
                self.draw_block(row)
        if self.now:
            moment, y = self.now
            ui.WARNING.set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(_RAIL_X - 4, y + 5, 8, 8)).fill()
            _draw(f"Now · {_clock(moment)}", ui.font(11.5, ui.MEDIUM), ui.WARNING, NSMakeRect(_CARD_X, y + 2, 200, 14))

    @objc.python_method
    def draw_time(self, moment: float, y: float) -> None:
        _draw(_clock(moment), ui.digits(11.5, ui.MEDIUM), ui.SECONDARY_TEXT, NSMakeRect(0, y + 7, _TIME_WIDTH, 14), right=True)

    @objc.python_method
    def draw_block(self, row) -> None:
        start, end, block, y, _ = row
        tempo = block.lane == timeline.TEMPO
        tint, solid = (ui.TEMPO_TINT, ui.TEMPO) if tempo else (ui.ACCENT_TINT, ui.ACCENT)
        self.draw_time(start, y)
        NSColor.windowBackgroundColor().set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(_RAIL_X - 7, y + 7, 14, 14)).fill()
        solid.set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(_RAIL_X - 5, y + 9, 10, 10)).fill()

        box = self.card_rect(row)
        shape = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, 8, 8)
        tint.set()
        shape.fill()
        if block.pending:  # just added, not saved yet
            solid.set()
            shape.setLineWidth_(1)
            shape.setLineDash_count_phase_([4, 3], 2, 0)
            shape.stroke()
        inner_x, inner_w = box.origin.x + 12, box.size.width - 24
        duration = _hm(int(end - start))
        _draw(duration, ui.digits(13, ui.MEDIUM), ui.PRIMARY_TEXT, NSMakeRect(inner_x + inner_w - 60, y + 7, 60, 16), right=True)
        name, key = _name_and_key(_block_title(block), block.jira_key)
        name_text = ui.name_text(name, 13, ui.MEDIUM)
        key_text = ui.key_text(key, 13, ui.MEDIUM, solid, link=self.can_open(block)) if key else None
        name_width, key_x = _name_then_key(name_text, key_text, inner_w - 70)
        name_text.drawWithRect_options_(NSMakeRect(inner_x, y + 7, name_width, 17), 1 | 32)  # one line, truncated
        if key_text is not None:
            key_text.drawWithRect_options_(NSMakeRect(inner_x + key_x, y + 7, key_text.size().width + 2, 17), 1)
        detail = f"{_clock(start)}–{_clock(end)}" + ("  ·  in Tempo" if tempo else "") + ("  ·  not saved yet" if block.pending else "")
        _draw(detail, ui.digits(11), ui.SECONDARY_TEXT, NSMakeRect(inner_x, y + 27, inner_w, 14))

    @objc.python_method
    def draw_gap(self, row) -> None:
        start, end, _, y, _ = row
        self.draw_time(start, y - 4)
        NSColor.windowBackgroundColor().set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(_RAIL_X - 7, y + 3, 14, 14)).fill()
        ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(_RAIL_X - 5, y + 5, 10, 10))
        ring.setLineWidth_(1.5)
        ring.setLineDash_count_phase_([2, 2], 2, 0)
        ui.WARNING.set()
        ring.stroke()

        box = self.card_rect(row)
        shape = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, 8, 8)
        ui.WARNING_TINT.set()
        shape.fill()
        shape.setLineWidth_(1)
        shape.setLineDash_count_phase_([4, 3], 2, 0)
        ui.WARNING.colorWithAlphaComponent_(0.6).set()
        shape.stroke()
        text = f"{_hm(int(end - start))} not tracked  ·  {_clock(start)}–{_clock(end)}"
        _draw(text, ui.font(12, ui.MEDIUM), ui.WARNING, NSMakeRect(box.origin.x + 12, y + 9, box.size.width - 120, 16))
        _draw("+ Fill with a ticket", ui.font(12, ui.SEMIBOLD), ui.ACCENT, NSMakeRect(box.origin.x + box.size.width - 170, y + 9, 158, 16), right=True)


def _inside(point, rect) -> bool:
    return rect.origin.x <= point.x <= rect.origin.x + rect.size.width and rect.origin.y <= point.y <= rect.origin.y + rect.size.height


def _draw(text: str, fnt, color, rect, right: bool = False) -> None:
    from AppKit import NSMutableParagraphStyle, NSParagraphStyleAttributeName

    style = NSMutableParagraphStyle.alloc().init()
    style.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
    if right:
        style.setAlignment_(2)
    attributes = {NSFontAttributeName: fnt, NSForegroundColorAttributeName: color, NSParagraphStyleAttributeName: style}
    NSAttributedString.alloc().initWithString_attributes_(text, attributes).drawInRect_(rect)


def _block_title(block: timeline.Block) -> str:
    return block.title or block.key


def _name_and_key(title: str, jira_key: str = "") -> tuple[str, str]:
    """What a ticket shows in the day overview: its name first, then its key.

    'IN-123 · Kassa' -> ('Kassa', 'IN-123'); a fixed option 'Support Work' with key IN-50 -> ('Support Work', 'IN-50');
    only a key -> ('', key). A text without a key, like '@Standup', has key ''.
    """
    name, key = ui.split_ticket(title)
    if not key and ISSUE_KEY_RE.match(name):
        name, key = "", name
    return name, key or jira_key


def _name_then_key(name_text, key_text, available: float) -> tuple[float, float]:
    """(width of the name, x of the key): a long name is cut off, the key always stays visible."""
    key_width = key_text.size().width + 2 if key_text is not None else 0
    gap = _NAME_KEY_GAP if key_text is not None and name_text.length() else 0
    name_width = max(0.0, min(name_text.size().width + 1, available - key_width - gap)) if name_text.length() else 0.0
    return name_width, name_width + gap


_NAME_KEY_GAP = 8


def _block_period(block: timeline.Block) -> str:
    return f"{_clock(block.start)}–{_clock(block.end)}  ·  {_hm(int(block.end - block.start))}"


def _dot(color) -> NSView:
    dot = ui.bar(8)
    dot.set_segments([(1.0, color)])
    ui.constrain(dot, width=8)
    return dot


def _hm(seconds: int) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"
