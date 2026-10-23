"""De vensters van de app, gebouwd met de bouwstenen uit ui.py."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import objc
from AppKit import (
    NSBeep,
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
)
from Foundation import NSObject

from . import ui


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

    @objc.python_method
    def select_row(self, row: int) -> None:
        from AppKit import NSIndexSet

        if not self.items:
            return
        row = max(0, min(row, len(self.items) - 1))
        self.table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        self.table.scrollRowToVisible_(row)

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
        if self.segments is not None and notification is not None and notification.object() == self.extra_field:
            if self.extra_field.stringValue().strip():
                self.segments.setSelectedSegment_(-1)  # een eigen tijd vervangt de knoppen
            return
        self.start_remote(self.search.stringValue())
        self.apply_filter(self.search.stringValue())

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
            if self.items:
                NSApp.stopModalWithCode_(0)
            else:
                NSBeep()  # niets om te kiezen: het venster blijft open
            return True
        if name in ("insertTab:", "insertBacktab:") and self.extra_field is not None:
            target = self.search if control == self.extra_field else self.extra_field
            control.window().makeFirstResponder_(target)
            return True
        return False

    def doubleClicked_(self, sender):
        NSApp.stopModalWithCode_(0)


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


def form(title: str, text: str, fields: list[FormField], buttons: tuple[str, ...] = ("Save", "Cancel")) -> dict | None:
    """Label/invoer-paren onder elkaar, in blokken met een kopje. None bij de laatste knop (annuleren)."""
    card = ui.Card(title, width=620)
    label_width, field_width = 170, card.inner - 170 - 12
    inputs = {}
    for item in fields:
        if item.section:
            card.add(ui.label(item.section, 13, ui.SEMIBOLD), after=6)
            if len(inputs):
                card.stack.setCustomSpacing_afterView_(18, card.stack.arrangedSubviews()[-2])
        caption = ui.label(item.label, 13, color=ui.SECONDARY_TEXT)
        caption.setAlignment_(2)
        ui.constrain(caption, width=label_width)
        field = _field(item.value, NSMakeRect(0, 0, field_width, 24), item.placeholder, item.secure)
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
    return {name: field.stringValue() for name, field in inputs.items()}


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


def overview(
    title: str,
    text: str,
    lines: list[OverviewLine],
    totals: Callable[[list[tuple[str, bool]]], tuple[str, int | None, int, int]] | None = None,
    chips: list[str] = (),
) -> tuple[str | None, list[OverviewLine]]:
    """Bewerkbare tabel per ticket. Geeft ('send' | 'save' | None, aangepaste regels) terug.

    `totals` krijgt (duur, verwijderd) per regel en geeft (tekst, in Tempo, te versturen, doel) in seconden;
    `chips` zijn de worklogs die al in Tempo staan.
    """
    card = ui.Card(title, text, width=900)
    row_height, header_height = 40, 24
    content_height = header_height + row_height * len(lines)
    scrolls = content_height > 400
    width = card.inner - (18 if scrolls else 0)  # ruimte voor de scrollbalk
    columns = {"ticket": 310, "tracked": 60, "duration": 64, "reset": 22, "description": width - 310 - 60 - 64 - 22 - 26 - 5 * 10, "remove": 26}
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

    # Tabel
    document = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, content_height))
    for name, header in (("ticket", "Ticket"), ("tracked", "Tracked"), ("duration", "Log"), ("description", "Description")):
        heading = ui.label(header, 11.5, ui.MEDIUM, ui.TERTIARY_TEXT)
        heading.setFrame_(NSMakeRect(x_positions[name], 2, columns[name], 16))
        if name in ("tracked", "duration"):
            heading.setAlignment_(2)
        document.addSubview_(heading)
    top_line = _separator(width)
    top_line.setFrameOrigin_((0, header_height - 2))
    document.addSubview_(top_line)

    widgets = []
    for n, line in enumerate(lines):
        y = header_height + n * row_height
        middle = y + (row_height - 24) / 2
        ticket_x, ticket_w = x_positions["ticket"], columns["ticket"]
        row_views = []
        key_field = None
        if line.target_key_editable:
            key_width = 120
            if line.label:
                name_label = ui.label(f"{line.label}  →", 13)
                name_label.setFrame_(NSMakeRect(ticket_x, middle + 3, ticket_w - key_width - 8, 18))
                document.addSubview_(name_label)
                row_views.append(name_label)
            else:
                key_width = ticket_w
            key_field = _field(
                line.target_key, NSMakeRect(ticket_x + ticket_w - key_width, middle, key_width, 24),
                "IN-100" if line.label else "Jira key, like IN-100",
            )
            document.addSubview_(key_field)
            row_views.append(key_field)
        else:
            name_label = ui.ticket_label(line.label, 13)
            name_label.setFrame_(NSMakeRect(ticket_x, middle + 3, ticket_w, 18))
            document.addSubview_(name_label)
            row_views.append(name_label)
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
        if n < len(lines) - 1:
            line_view = _separator(width)
            line_view.setFrameOrigin_((0, y + row_height - 1))
            document.addSubview_(line_view)
        widgets.append((line, key_field, duration, description, remover, resetter))

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, card.inner, min(content_height, 400)))
    scroll.setHasVerticalScroller_(scrolls)
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(NSNoBorder)
    scroll.setDocumentView_(document)
    document.scrollPoint_((0, 0))
    card.add(ui.fixed(scroll, card.inner, min(content_height, 400)))

    updater = None
    if totals:
        def refresh():
            text_value, in_tempo, to_send, target = totals([(w[2].stringValue(), w[4].removed) for w in widgets])
            summary, _, verdict = text_value.partition("  ·  ")
            totals_label.setStringValue_(summary)
            status.setStringValue_(verdict)
            status.setTextColor_(ui.SUCCESS if "complete" in verdict else ui.WARNING)
            total = (in_tempo or 0) + to_send
            scale = max(target or 0, total, 1)
            progress.set_segments([((in_tempo or 0) / scale, ui.TEMPO), (to_send / scale, ui.ACCENT_FILL)])
            legend_tempo.setStringValue_(f"In Tempo {_hm(in_tempo or 0)}" if in_tempo is not None else "In Tempo: couldn't read")
            legend_send.setStringValue_(f"To send {_hm(to_send)}")

        updater = _TotalsUpdater.alloc().initWithRefresh_(refresh)
        for _, _, duration, _, remover, resetter in widgets:
            duration.setDelegate_(updater)
            remover.on_change = refresh
            if resetter:
                resetter.on_change = refresh
        refresh()

    card.buttons(
        [ui.button("Cancel", CANCEL), ui.button("Save", SAVE), ui.button("Send to Tempo", SEND, primary=True)],
        cancel_code=CANCEL,
    )
    card.first_responder = widgets[0][2] if widgets else None
    button = card.run()

    edited = []
    for line, key_field, duration, description, remover, _ in widgets:
        edited.append(
            OverviewLine(
                issue_key=line.issue_key,
                label=line.label,
                tracked=line.tracked,
                target_key=key_field.stringValue().strip().upper() if key_field else line.target_key,
                target_key_editable=line.target_key_editable,
                duration=duration.stringValue(),
                description=description.stringValue().strip(),
                description_placeholder=line.description_placeholder,
                removed=remover.removed,
                auto_duration=line.auto_duration,
            )
        )
    return {SEND: "send", SAVE: "save"}.get(button), edited


def _dot(color) -> NSView:
    dot = ui.bar(8)
    dot.set_segments([(1.0, color)])
    ui.constrain(dot, width=8)
    return dot


def _hm(seconds: int) -> str:
    minutes = int(round(seconds / 60))
    return f"{minutes // 60}:{minutes % 60:02d}"
