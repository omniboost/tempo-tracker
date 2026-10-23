"""Bouwstenen voor de vensters: Tempo-blauw, lettertypes, labels, chips, balken, knoppen en een kaartvenster.

Alles is native AppKit en volgt de lichte en donkere modus van macOS.
"""

from __future__ import annotations

import objc
from AppKit import (
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApp,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSControlSizeLarge,
    NSEdgeInsetsMake,
    NSEvent,
    NSFloatingWindowLevel,
    NSFont,
    NSFontAttributeName,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageLeading,
    NSImageView,
    NSLayoutAttributeCenterY,
    NSLayoutAttributeLeading,
    NSLayoutAttributeTop,
    NSMakeRect,
    NSMakeSize,
    NSModalPanelRunLoopMode,
    NSModalResponseAbort,
    NSMutableAttributedString,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPanel,
    NSPointInRect,
    NSScreen,
    NSStackView,
    NSTableRowView,
    NSTextTab,
    NSTextField,
    NSInsetRect,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
    NSWindowAnimationBehaviorNone,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

TIMED_OUT = -1
ENTER_DELAY = 1.0  # Enter werkt pas na een seconde: een venster dat opduikt terwijl je typt, vangt anders jouw Enter op


# -- kleuren -------------------------------------------------------------------


def _hex(value: str, alpha: float = 1.0) -> NSColor:
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def _dynamic(light: NSColor, dark: NSColor) -> NSColor:
    def provider(appearance):
        best = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
        return dark if best == NSAppearanceNameDarkAqua else light

    return NSColor.colorWithName_dynamicProvider_(None, provider)


ACCENT = _dynamic(_hex("#0C66E4"), _hex("#579DFF"))  # Tempo-/Atlassian-blauw
ACCENT_FILL = _dynamic(_hex("#0C66E4"), _hex("#1D7AFC"))  # knoppen: donker genoeg voor witte tekst
ACCENT_TINT = _dynamic(_hex("#0C66E4", 0.12), _hex("#579DFF", 0.22))
TEMPO = _dynamic(_hex("#6E5DC6"), _hex("#9F8FEF"))  # al in Tempo (paars)
TEMPO_TINT = _dynamic(_hex("#6E5DC6", 0.12), _hex("#9F8FEF", 0.22))
WARNING = _dynamic(_hex("#B65C02"), _hex("#FCA700"))
WARNING_TINT = _dynamic(_hex("#FCA700", 0.18), _hex("#FCA700", 0.20))
SUCCESS = _dynamic(_hex("#1F845A"), _hex("#4BCE97"))
TRACK = _dynamic(_hex("#091E42", 0.08), _hex("#FFFFFF", 0.10))
PRIMARY_TEXT = NSColor.labelColor()
SECONDARY_TEXT = NSColor.secondaryLabelColor()
TERTIARY_TEXT = NSColor.tertiaryLabelColor()


# -- lettertypes ---------------------------------------------------------------


def font(size: float = 13, weight=NSFontWeightRegular) -> NSFont:
    return NSFont.systemFontOfSize_weight_(size, weight)


def mono(size: float = 13, weight=NSFontWeightMedium) -> NSFont:
    return NSFont.monospacedSystemFontOfSize_weight_(size, weight)


def digits(size: float = 13, weight=NSFontWeightRegular) -> NSFont:
    return NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight)


SEMIBOLD, MEDIUM = NSFontWeightSemibold, NSFontWeightMedium


# -- tekst ---------------------------------------------------------------------


def label(text: str, size: float = 13, weight=NSFontWeightRegular, color=None, wrap_width: float | None = None, fnt=None):
    field = NSTextField.wrappingLabelWithString_(text) if wrap_width else NSTextField.labelWithString_(text)
    field.setFont_(fnt or font(size, weight))
    field.setTextColor_(color or PRIMARY_TEXT)
    if wrap_width:
        field.setPreferredMaxLayoutWidth_(wrap_width)
    else:
        field.setLineBreakMode_(4)  # afkappen met …
    field.setSelectable_(False)
    return field


def ticket_text(text: str, size: float = 13, weight=NSFontWeightRegular, key_color=None) -> NSAttributedString:
    """'IN-123 · Titel' -> key in het blauw (monospace) + titel. Tekst zonder key blijft gewoon."""
    key, sep, summary = text.partition(" · ")
    result = NSMutableAttributedString.alloc().init()
    if sep and key and key[0].isalpha() and "-" in key:
        result.appendAttributedString_(_styled(key, mono(size - 0.5, SEMIBOLD if weight == SEMIBOLD else MEDIUM), key_color or ACCENT))
        result.appendAttributedString_(_styled("  " + summary, font(size, weight), PRIMARY_TEXT))
    else:
        result.appendAttributedString_(_styled(text, font(size, weight), PRIMARY_TEXT))
    return result


def _styled(text: str, fnt, color) -> NSAttributedString:
    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setLineBreakMode_(4)  # afkappen met …
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSFontAttributeName: fnt, NSForegroundColorAttributeName: color, NSParagraphStyleAttributeName: paragraph}
    )


def columns_text(parts: list[tuple[str, object, object]], tabs: list[float]) -> NSAttributedString:
    """Tekst in kolommen (tab-stops), bijv. voor een menuregel: [(tekst, lettertype, kleur), …]."""
    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setTabStops_([NSTextTab.alloc().initWithTextAlignment_location_options_(0, x, {}) for x in tabs])
    result = NSMutableAttributedString.alloc().init()
    for n, (text, fnt, color) in enumerate(parts):
        piece = ("\t" if n else "") + text
        result.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            piece, {NSFontAttributeName: fnt, NSForegroundColorAttributeName: color, NSParagraphStyleAttributeName: paragraph}
        ))
    return result


def ticket_label(text: str, size: float = 13, weight=NSFontWeightRegular, wrap_width: float | None = None):
    field = label("", size, wrap_width=wrap_width)
    field.setAttributedStringValue_(ticket_text(text, size, weight))
    if not wrap_width:
        field.cell().setTruncatesLastVisibleLine_(True)
        field.setLineBreakMode_(4)
    field.setToolTip_(text)
    return field


def symbol(name: str, size: float = 13, color=None):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    view = NSImageView.imageViewWithImage_(image) if image else NSImageView.alloc().init()
    view.setContentTintColor_(color or SECONDARY_TEXT)
    view.setSymbolConfiguration_(_symbol_config(size))
    return view


def _symbol_config(size: float):
    from AppKit import NSImageSymbolConfiguration

    return NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, NSFontWeightMedium)


def symbol_image(name: str, size: float = 13):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    return image.imageWithSymbolConfiguration_(_symbol_config(size)) if image else None


# -- vormen --------------------------------------------------------------------


class Chip(NSView):
    """Afgerond label met een gekleurde achtergrond, bijv. een meeting die al in Tempo staat."""

    def initWithText_color_tint_(self, text, color, tint):
        self = objc.super(Chip, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        if self is None:
            return None
        self.tint = tint
        self.text = label(text, 12, MEDIUM, color)
        self.text.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.addSubview_(self.text)
        constrain(self.text, leading=(self, 9), trailing=(self, -9), top=(self, 3), bottom=(self, -3))
        return self

    def drawRect_(self, rect):
        self.tint.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 6, 6).fill()


def chip(text: str, color=None, tint=None) -> Chip:
    view = Chip.alloc().initWithText_color_tint_(text, color or ACCENT, tint or ACCENT_TINT)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return view


class Bar(NSView):
    """Voortgangsbalk met gekleurde stukken: [(aandeel 0..1, kleur), …] op een grijze ondergrond."""

    def initWithHeight_(self, height):
        self = objc.super(Bar, self).initWithFrame_(NSMakeRect(0, 0, 100, height))
        if self is None:
            return None
        self.segments = []
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def set_segments(self, segments) -> None:
        self.segments = segments
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        TRACK.setFill()
        track.fill()
        track.addClip()
        x = 0.0
        for fraction, color in self.segments:
            width = max(0.0, min(fraction, 1.0)) * bounds.size.width
            color.setFill()
            NSBezierPath.fillRect_(NSMakeRect(x, 0, width, bounds.size.height))
            x += width


def bar(height: float = 8) -> Bar:
    view = Bar.alloc().initWithHeight_(height)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    view.heightAnchor().constraintEqualToConstant_(height).setActive_(True)
    return view


# -- layout --------------------------------------------------------------------


def constrain(view, leading=None, trailing=None, top=None, bottom=None, width=None, height=None) -> None:
    """Kleine helper voor Auto Layout: (anderView, afstand) per rand, of vaste maten."""
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    for anchor, spec, other_anchor in (
        ("leadingAnchor", leading, "leadingAnchor"),
        ("trailingAnchor", trailing, "trailingAnchor"),
        ("topAnchor", top, "topAnchor"),
        ("bottomAnchor", bottom, "bottomAnchor"),
    ):
        if spec is not None:
            other, constant = spec
            getattr(view, anchor)().constraintEqualToAnchor_constant_(getattr(other, other_anchor)(), constant).setActive_(True)
    if width is not None:
        view.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    if height is not None:
        view.heightAnchor().constraintEqualToConstant_(height).setActive_(True)


def row(*views, spacing: float = 8, align=NSLayoutAttributeCenterY) -> NSStackView:
    stack = NSStackView.stackViewWithViews_([v for v in views if v is not None])
    stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    stack.setAlignment_(align)
    stack.setSpacing_(spacing)
    return stack


def column(*views, spacing: float = 6) -> NSStackView:
    stack = NSStackView.stackViewWithViews_([v for v in views if v is not None])
    stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
    stack.setAlignment_(NSLayoutAttributeLeading)
    stack.setSpacing_(spacing)
    return stack


def spacer() -> NSView:
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 1))
    view.setContentHuggingPriority_forOrientation_(1, 0)
    return view


def fixed(view, width: float, height: float):
    """Een view met frames (bijv. een tabel) als vast blok in een stack."""
    constrain(view, width=width, height=height)
    return view


# -- knoppen -------------------------------------------------------------------


class _Responder(NSObject):
    def clicked_(self, sender):
        NSApp.stopModalWithCode_(sender.tag())


_RESPONDER = _Responder.alloc().init()


def button(title: str, code: int, primary: bool = False, large: bool = True) -> NSButton:
    """Knop die het venster sluit met `code`. De hoofdknop is Tempo-blauw en reageert op Enter."""
    view = NSButton.buttonWithTitle_target_action_(title, _RESPONDER, "clicked:")
    view.setTag_(code)
    if large:
        view.setControlSize_(NSControlSizeLarge)
    if primary:
        view.setKeyEquivalent_("\r")
        view.setBezelColor_(ACCENT_FILL)
    return view


def link_button(title: str, code: int, symbol_name: str | None = None) -> NSButton:
    """Rustige knop zonder rand (bijv. Break / Later)."""
    view = NSButton.buttonWithTitle_target_action_(title, _RESPONDER, "clicked:")
    view.setTag_(code)
    view.setBordered_(False)
    view.setFont_(font(12.5))
    view.setContentTintColor_(SECONDARY_TEXT)
    if symbol_name:
        view.setImage_(symbol_image(symbol_name, 12))
        view.setImagePosition_(NSImageLeading)
        view.setImageHugsTitle_(True)
    return view


# -- kaartvenster ----------------------------------------------------------------


class _CardPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):
        code = getattr(self, "cancel_code", None)
        if code is not None:
            NSApp.stopModalWithCode_(code)


class Card:
    """Een eigen venster: koptekst, inhoud onder elkaar en knoppen rechtsonder. `run()` wacht op een keuze."""

    def __init__(self, title: str, subtitle: str = "", width: float = 440, icon: bool = True):
        self.width = width
        self.inner = width - 48
        self.panel = _CardPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, 200), NSWindowStyleMaskTitled | NSWindowStyleMaskFullSizeContentView,
            NSBackingStoreBuffered, False,
        )
        self.panel.setTitlebarAppearsTransparent_(True)
        self.panel.setTitleVisibility_(1)  # verborgen
        self.panel.setMovableByWindowBackground_(True)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.cancel_code = None
        self.stack = column(spacing=12)
        self.stack.setEdgeInsets_(NSEdgeInsetsMake(30, 24, 20, 24))
        self.panel.setContentView_(self.stack)
        constrain(self.stack, width=width)
        self.default_button = None
        self.first_responder = None

        title_view = label(title, 16, SEMIBOLD, wrap_width=self.inner - 44)
        heading = column(title_view, label(subtitle, 12.5, color=SECONDARY_TEXT, wrap_width=self.inner - 44) if subtitle else None, spacing=2)
        if icon and NSApp.applicationIconImage():
            image = NSImageView.imageViewWithImage_(NSApp.applicationIconImage())
            fixed(image, 36, 36)
            heading = row(image, heading, spacing=12, align=NSLayoutAttributeTop if subtitle else NSLayoutAttributeCenterY)
        self.add(heading, after=16)

    def add(self, view, after: float | None = None, full_width: bool = False):
        if view is None:
            return None
        self.stack.addArrangedSubview_(view)
        if after is not None:
            self.stack.setCustomSpacing_afterView_(after, view)
        if full_width:
            view.setTranslatesAutoresizingMaskIntoConstraints_(False)
            view.widthAnchor().constraintEqualToConstant_(self.inner).setActive_(True)
        return view

    def text(self, text: str, size: float = 13, color=None, after: float | None = None):
        if text:
            self.add(label(text, size, color=color or SECONDARY_TEXT, wrap_width=self.inner), after=after)

    def buttons(self, buttons: list[NSButton], secondary: list[NSView] = (), cancel_code: int | None = None) -> None:
        """Knoppen rechtsonder (laatste = rechts); `secondary` als rustige regel daaronder."""
        self.default_button = next((b for b in buttons if b.keyEquivalent() == "\r"), None)
        self.panel.cancel_code = cancel_code
        self.stack.setCustomSpacing_afterView_(20, self.stack.arrangedSubviews()[-1])
        self.add(row(spacer(), *buttons, spacing=8), full_width=True, after=6 if secondary else None)
        if secondary:
            self.add(row(spacer(), *secondary, spacing=14), full_width=True)

    def run(self, timeout: float | None = None) -> int:
        """Toon het venster bovenop alles en wacht. Geeft de code van de gekozen knop, of TIMED_OUT."""
        panel = self.panel
        panel.layoutIfNeeded()
        panel.setContentSize_(self.stack.fittingSize())
        panel.setLevel_(NSFloatingWindowLevel)
        # Geen verschijn-animatie: die kon blijven hangen als de app op de achtergrond draaide.
        panel.setAnimationBehavior_(NSWindowAnimationBehaviorNone)
        # Op het huidige bureaublad tonen, ook boven een app op volledig scherm.
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
        _center_on_mouse_screen(panel)
        if self.first_responder is not None:
            panel.setInitialFirstResponder_(self.first_responder)
        return run_modal(panel, self.default_button, timeout)


# -- modaal tonen ----------------------------------------------------------------


def activate() -> None:
    NSApp.activateIgnoringOtherApps_(True)


class _Fronter(NSObject):
    """Zet het venster na het openen nogmaals naar voren, ook als macOS de app niet actief maakt."""

    def initWithWindow_(self, window):
        self = objc.super(_Fronter, self).init()
        if self is None:
            return None
        self.window = window
        self.default_button = None
        return self

    def front_(self, _timer):
        activate()
        self.window.orderFrontRegardless()
        self.window.makeKeyWindow()

    def expire_(self, _timer):
        NSApp.abortModal()

    def arm_(self, _timer):
        if self.default_button is not None:
            self.default_button.setKeyEquivalent_("\r")


def _center_on_mouse_screen(window) -> None:
    """Midden (iets boven het midden) op het scherm waar je muis staat; daar kijk je waarschijnlijk naar."""
    mouse = NSEvent.mouseLocation()
    screen = next((s for s in NSScreen.screens() if NSPointInRect(mouse, s.frame())), NSScreen.mainScreen())
    area, size = screen.visibleFrame(), window.frame().size
    x = area.origin.x + (area.size.width - size.width) / 2
    y = area.origin.y + (area.size.height - size.height) * 2 / 3
    window.setFrameOrigin_((x, y))


def run_modal(window, default_button=None, timeout: float | None = None) -> int:
    activate()
    fronter = _Fronter.alloc().initWithWindow_(window)
    timers = [NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.3, fronter, "front:", None, False)]
    if default_button is not None:
        fronter.default_button = default_button
        default_button.setKeyEquivalent_("")
        timers.append(NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(ENTER_DELAY, fronter, "arm:", None, False))
    if timeout:
        timers.append(NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(timeout, fronter, "expire:", None, False))
    for timer in timers:
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)
    try:
        response = int(NSApp.runModalForWindow_(window))
    finally:
        for timer in timers:
            timer.invalidate()
        window.orderOut_(None)
    return TIMED_OUT if response == NSModalResponseAbort else response


class _MainThreadCall(NSObject):
    def call_(self, payload):
        function, args = payload
        function(*args)


_MAIN_THREAD = _MainThreadCall.alloc().init()


def on_main(function, *args) -> None:
    """Voer `function(*args)` uit op de hoofdthread, ook terwijl een venster modaal open staat."""
    _MAIN_THREAD.performSelectorOnMainThread_withObject_waitUntilDone_modes_(
        "call:", (function, args), False, [NSRunLoopCommonModes]
    )


def spinner():
    """Klein draaiend wieltje (bezig)."""
    from AppKit import NSProgressIndicator, NSProgressIndicatorStyleSpinning

    view = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
    view.setStyle_(NSProgressIndicatorStyleSpinning)
    view.setControlSize_(1)  # klein
    view.setIndeterminate_(True)
    view.startAnimation_(None)
    return fixed(view, 16, 16)


def bring_to_front() -> bool:
    """Haal een openstaand venster naar voren (bijv. via de sneltoets). False als er geen open is."""
    window = NSApp.modalWindow()
    if window is None:
        return False
    activate()
    window.orderFrontRegardless()
    window.makeKeyWindow()
    return True


class SelectionRow(NSTableRowView):
    """Tabelrij met een lichte blauwe selectie, zodat de blauwe ticketkey leesbaar blijft."""

    def drawSelectionInRect_(self, rect):
        if self.selectionHighlightStyle() != -1:  # -1 = geen selectie tekenen
            ACCENT_TINT.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSInsetRect(self.bounds(), 4, 1), 6, 6).fill()

    def interiorBackgroundStyle(self):
        return 0  # NSBackgroundStyleNormal: tekst niet wit maken, ook niet als de tabel actief is (de selectie is licht)


class MenuHeader(NSView):
    """Kop van het menubalk-menu: status, huidig ticket, sinds wanneer en de voortgang van vandaag."""

    WIDTH, HEIGHT = 320, 96

    def init(self):
        self = objc.super(MenuHeader, self).initWithFrame_(NSMakeRect(0, 0, self.WIDTH, self.HEIGHT))
        if self is None:
            return None
        self.dot = bar(8)
        self.dot.setTranslatesAutoresizingMaskIntoConstraints_(True)
        self.dot.setFrame_(NSMakeRect(16, 15, 8, 8))
        self.ticket = label("", 13, SEMIBOLD)
        self.ticket.setFrame_(NSMakeRect(32, 10, self.WIDTH - 46, 18))
        self.detail = label("", 12, color=SECONDARY_TEXT)
        self.detail.setFrame_(NSMakeRect(32, 29, self.WIDTH - 46, 16))
        self.today = label("Today", 12, MEDIUM, SECONDARY_TEXT)
        self.today.setFrame_(NSMakeRect(16, 54, 120, 16))
        self.total = label("", fnt=digits(12, MEDIUM), color=SECONDARY_TEXT)
        self.total.setAlignment_(2)
        self.total.setFrame_(NSMakeRect(self.WIDTH - 196, 54, 180, 16))
        self.progress = bar(6)
        self.progress.setTranslatesAutoresizingMaskIntoConstraints_(True)
        self.progress.setFrame_(NSMakeRect(16, 74, self.WIDTH - 32, 6))
        for view in (self.dot, self.ticket, self.detail, self.today, self.total, self.progress):
            self.addSubview_(view)
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def update(self, state: str, ticket: str, detail: str, total: str, fraction: float) -> None:
        """state: 'tracking' (groen), 'away' (oranje) of 'paused' (grijs)."""
        color = {"tracking": SUCCESS, "away": WARNING}.get(state, TERTIARY_TEXT)
        self.dot.set_segments([(1.0, color)])
        self.ticket.setAttributedStringValue_(ticket_text(ticket, 13, SEMIBOLD))
        self.detail.setStringValue_(detail)
        self.total.setStringValue_(total)
        self.progress.set_segments([(fraction, ACCENT_FILL)])


def centered(view) -> NSView:
    """Zet een label verticaal in het midden van een tabelcel."""
    cell = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    cell.addSubview_(view)
    view.leadingAnchor().constraintEqualToAnchor_(cell.leadingAnchor()).setActive_(True)
    view.trailingAnchor().constraintEqualToAnchor_(cell.trailingAnchor()).setActive_(True)
    view.centerYAnchor().constraintEqualToAnchor_(cell.centerYAnchor()).setActive_(True)
    return cell


def size(width: float, height: float):
    return NSMakeSize(width, height)
