"""Screenshots voor de README: de echte vensters met verzonnen data, licht en donker, naar docs/images/.

Draaien vanuit de repo: `.venv/bin/python -m scripts.screenshots`. Er verschijnt geen venster; `ui.run_modal`
tekent het venster naar een PNG in plaats van het te tonen. Je eigen database, config en Keychain blijven onaangeroerd.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from AppKit import (
    NSAppearance,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBitmapImageFileTypePNG,
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSShadow,
)

from tempo_tracker import app as app_module
from tempo_tracker import dialogs, ui
from tempo_tracker.config import Config
from tempo_tracker.dialogs import PickItem, WeekRow
from tempo_tracker.icon import app_icon
from tempo_tracker.tempo import TempoWorklog

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SCALE = 2  # Retina-scherpte, ook op een scherm zonder Retina
MARGIN, RADIUS = 24, 12  # ruimte voor de schaduw rond de kaart, en de ronde hoeken van een macOS-venster
THEMES = {"light": "NSAppearanceNameAqua", "dark": "NSAppearanceNameDarkAqua"}
DAY = "2026-10-19"  # een maandag in het verleden, zodat het dagoverzicht niet aan 'vandaag' komt

FIXED = [
    {"label": "Standup", "issue_key": "DEMO-100"},
    {"label": "Refinement", "issue_key": "DEMO-101"},
    {"label": "Support", "issue_key": "DEMO-7"},
]
TICKETS = [
    PickItem("DEMO-12", "DEMO-12 · Split bills per guest", "recently used"),
    PickItem("DEMO-31", "DEMO-31 · Receipt totals off by a cent", "recently used"),
    PickItem("DEMO-27", "DEMO-27 · Export invoices as PDF", "assigned"),
    PickItem("DEMO-44", "DEMO-44 · Retry failed payments", "assigned"),
    PickItem("DEMO-9", "DEMO-9 · Dark mode for the receipt view", "recent in Jira"),
]
FIXED_ITEMS = [PickItem(f"@{o['label']}", o["label"], "fixed") for o in FIXED]

_shot: dict = {}


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 19, hour, minute).timestamp()


def _bitmap(width: float, height: float) -> NSBitmapImageRep:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, int(width * SCALE), int(height * SCALE), 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
    )
    rep.setSize_((width, height))
    return rep


def _render(window, path: Path, theme: str) -> None:
    """Het venster als PNG, zoals op je scherm: ronde hoeken, dunne rand en schaduw op een transparante achtergrond."""
    window.setAppearance_(NSAppearance.appearanceNamed_(THEMES[theme]))
    window.layoutIfNeeded()
    view = window.contentView().superview()  # het hele venster, met achtergrond
    width, height = view.bounds().size
    card = _bitmap(width, height)
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), card)
    image = NSImage.alloc().initWithSize_((width, height))
    image.addRepresentation_(card)

    canvas = _bitmap(width + 2 * MARGIN, height + 2 * MARGIN)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(canvas))
    frame = NSMakeRect(MARGIN, MARGIN, width, height)
    outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(frame, RADIUS, RADIUS)
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(NSColor.colorWithWhite_alpha_(0, 0.28))
    shadow.setShadowBlurRadius_(18)
    shadow.setShadowOffset_((0, -6))
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    NSColor.blackColor().set()
    outline.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.saveGraphicsState()
    outline.addClip()
    image.drawInRect_(frame)
    NSGraphicsContext.restoreGraphicsState()
    NSColor.colorWithWhite_alpha_(1, 0.14).set() if theme == "dark" else NSColor.colorWithWhite_alpha_(0, 0.14).set()
    outline.setLineWidth_(1)
    outline.stroke()
    NSGraphicsContext.restoreGraphicsState()
    canvas.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(path), True)


def _capture(window, default_button=None, timeout=None) -> int:
    """Vervangt ui.run_modal: venster naar PNG (licht en donker) en meteen 'geannuleerd' teruggeven."""
    if _shot.get("prepare"):
        _shot["prepare"](window)
    for theme in THEMES:
        _render(window, OUT / f"{_shot['name']}-{theme}.png", theme)
        print(f"  {_shot['name']}-{theme}.png")
    window.orderOut_(None)
    return ui.TIMED_OUT


def shoot(name: str, show, prepare=None) -> None:
    _shot.update(name=name, prepare=prepare)
    show()


def _type_in_picker(text: str, from_jira: list[PickItem]):
    """Typ `text` in het zoekveld en toon `from_jira` als zoekresultaten uit Jira."""

    def prepare(window) -> None:
        search = window.initialFirstResponder()
        controller = search.delegate()
        search.setStringValue_(text)
        controller.remote_items = from_jira
        controller.apply_filter(text)

    return prepare


def make_app(tmp: str):
    """De echte app met een tijdelijke database, demo-instellingen en nep-Jira/Tempo (geen netwerk)."""
    config = Config(
        jira_site="company.atlassian.net", email="me@example.com", fixed_options=FIXED,
        git_folders="", shortcut="", week_reminder="fri 15:00",
    )
    patches = [
        mock.patch.object(app_module, "DB_PATH", Path(tmp) / "demo.db"),
        mock.patch.object(app_module, "load_config", return_value=config),
        mock.patch.object(app_module, "save_config"),
        mock.patch.object(app_module.credentials, "get_token", return_value="token"),
        mock.patch.object(dialogs, "progress", lambda title, text, work: work(lambda line: None)),
    ]
    for patch in patches:
        patch.start()
    app = app_module.TempoTrackerApp()
    app.timer.stop()
    app._last_refresh = app._last_week_refresh = float("inf")

    jira, tempo = mock.Mock(), mock.Mock()
    jira.myself.return_value = "demo-account"
    tempo.worklogs.return_value = [TempoWorklog("w-1", "1101", "14:00:00", 3600, "Refinement", DAY)]
    app._jira, app._tempo = (lambda: jira), (lambda: tempo)
    app._issue_key_for_id = lambda _jira, _issue_id: "DEMO-101"
    app._commit_descriptions = lambda day: {"DEMO-12": "Split bills per guest; Show the split on the receipt"}

    store = app.store
    store.add_segment("DEMO-12", "DEMO-12 · Split bills per guest", at(9), at(9, 30))
    store.add_segment("@Standup", "Standup", at(9, 30), at(9, 45))
    store.add_segment("DEMO-12", "DEMO-12 · Split bills per guest", at(9, 45), at(12, 15))
    store.add_segment("DEMO-31", "DEMO-31 · Receipt totals off by a cent", at(13), at(13, 50))
    store.add_segment("DEMO-12", "DEMO-12 · Split bills per guest", at(13, 50), at(15, 30))
    store.add_segment("@Support", "Support", at(15, 30), at(17, 15))
    return app, patches


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    application.setApplicationIconImage_(app_icon())
    ui.run_modal = _capture

    shoot("checkin", lambda: dialogs.checkin(
        "DEMO-12 · Split bills per guest", "0:45", "10:15",
        [("Standup · call 09:30–09:47", "0:17"), ("DEMO-12 · Split bills per guest (rest)", "0:28")],
    ))
    shoot("switch", lambda: dialogs.pick_with_since(
        "Switch ticket", "From now (or from the start time) your time goes to this ticket.",
        TICKETS + FIXED_ITEMS, "DEMO-12", ["Now", "5m ago", "10m", "15m", "30m"],
    ), _type_in_picker("bills", [
        PickItem("DEMO-58", "DEMO-58 · Split bills: round per guest", "Jira"),
        PickItem("DEMO-61", "DEMO-61 · Print split bills on the kitchen printer", "Jira"),
    ]))
    shoot("call", lambda: dialogs.pick(
        "You were in a call",
        "09:30–09:47 (0:17). What was it?\nSuggested: you picked Standup the last 2 times around 09:30.",
        [PickItem(f"@{o['label']}", o["label"], o["issue_key"]) for o in FIXED]
        + [PickItem("DEMO-12", "DEMO-12 · Split bills per guest", "current ticket")] + TICKETS[1:],
        "@Standup", buttons=("Log", "Keep on DEMO-12"),
    ))
    shoot("week", lambda: dialogs.week(
        "Your week", "Everything that's in Tempo plus what's still waiting to be sent.",
        [
            WeekRow("Mon 19", "8:00", "✓ in Tempo", "done"),
            WeekRow("Tue 20", "7:45", "✓ in Tempo", "done"),
            WeekRow("Wed 21", "8:15", "✓ in Tempo", "done"),
            WeekRow("Thu 22", "6:30", "6:30 not sent", "open"),
            WeekRow("Fri 23", "5:15", "today", "empty"),
        ],
        "35:45 of 40:00 this week", 35.75 / 40, ["Thursday: 6:30 not sent yet"], ["Open Thursday", "Later"],
    ))

    with tempfile.TemporaryDirectory() as tmp:
        app, patches = make_app(tmp)
        try:
            shoot("overview", lambda: app._overview(DAY, False))
            shoot("settings", app._settings)
        finally:
            for patch in patches:
                patch.stop()


if __name__ == "__main__":
    main()
