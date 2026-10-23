"""App-icoon: een klok op een blauwe tegel, met een oranje sector voor het check-in-interval van 45 minuten."""

from __future__ import annotations

import math

from AppKit import (
    NSBezierPath,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSShadow,
)


def _rgb(hex_color: str, alpha: float = 1.0) -> NSColor:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def _point(cx: float, cy: float, radius: float, minutes: float):
    """Punt op de wijzerplaat; 0 minuten = boven, met de klok mee."""
    angle = math.radians(90 - minutes * 6)
    return NSMakePoint(cx + radius * math.cos(angle), cy + radius * math.sin(angle))


def _draw(size: float) -> None:
    s = size / 1024  # alles getekend op een raster van 1024

    # Tegel met de macOS-marge (100 px rondom) en zachte schaduw.
    tile = NSMakeRect(100 * s, 100 * s, 824 * s, 824 * s)
    NSGraphicsContext.saveGraphicsState()
    shadow = NSShadow.alloc().init()
    shadow.setShadowOffset_(NSMakeSize(0, -12 * s))
    shadow.setShadowBlurRadius_(28 * s)
    shadow.setShadowColor_(_rgb("#0B1F4D", 0.35))
    shadow.set()
    tile_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(tile, 185 * s, 185 * s)
    _rgb("#1E40AF").setFill()
    tile_path.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(_rgb("#3B82F6"), _rgb("#1E3A8A")).drawInBezierPath_angle_(tile_path, -90)

    cx, cy, radius = 512 * s, 512 * s, 300 * s

    # Wijzerplaat met schaduw.
    NSGraphicsContext.saveGraphicsState()
    shadow = NSShadow.alloc().init()
    shadow.setShadowOffset_(NSMakeSize(0, -10 * s))
    shadow.setShadowBlurRadius_(24 * s)
    shadow.setShadowColor_(_rgb("#000000", 0.30))
    shadow.set()
    _rgb("#FFFFFF").setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - radius, cy - radius, radius * 2, radius * 2)).fill()
    NSGraphicsContext.restoreGraphicsState()

    # Oranje sector: 45 minuten sinds de vorige check-in.
    sector = NSBezierPath.bezierPath()
    sector.moveToPoint_(NSMakePoint(cx, cy))
    sector.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        NSMakePoint(cx, cy), radius - 34 * s, 90, 90 - 45 * 6, True
    )
    sector.closePath()
    _rgb("#FDBA74", 0.8).setFill()
    sector.fill()

    # Streepjes: dik op de kwartieren.
    _rgb("#1E3A8A").setStroke()
    for minute in range(0, 60, 5):
        major = minute % 15 == 0
        tick = NSBezierPath.bezierPath()
        tick.setLineWidth_((22 if major else 12) * s)
        tick.setLineCapStyle_(1)
        tick.moveToPoint_(_point(cx, cy, radius - (78 if major else 62) * s, minute))
        tick.lineToPoint_(_point(cx, cy, radius - 34 * s, minute))
        tick.stroke()

    # Wijzers: uur op 9, minuut op 12 (begin en einde van het interval).
    for minutes, length, width in ((45, 150, 34), (0, 220, 24)):
        hand = NSBezierPath.bezierPath()
        hand.setLineWidth_(width * s)
        hand.setLineCapStyle_(1)
        hand.moveToPoint_(NSMakePoint(cx, cy))
        hand.lineToPoint_(_point(cx, cy, length * s, minutes))
        _rgb("#0F172A").setStroke()
        hand.stroke()

    # Middelpunt in Tempo-oranje.
    _rgb("#F97316").setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 34 * s, cy - 34 * s, 68 * s, 68 * s)).fill()
    _rgb("#FFFFFF").setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 12 * s, cy - 12 * s, 24 * s, 24 * s)).fill()


def app_icon(size: int = 512) -> NSImage:
    image = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    image.lockFocus()
    _draw(size)
    image.unlockFocus()
    return image
