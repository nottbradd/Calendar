from __future__ import annotations

import ctypes
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QDir, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QLabel, QPushButton, QWidget


INK = "#20313B"
MUTED = "#485C69"
ACCENT = "#345E70"
ASSETS = Path(__file__).with_name("assets")
PATHS = {
    "plus": "M12 5v14M5 12h14",
    "previous": "m14.5 5.5-6.5 6.5 6.5 6.5",
    "next": "m9.5 5.5 6.5 6.5-6.5 6.5",
    "arrow": "M4 12h15m-6-6 6 6-6 6",
    "calendar": "M7 3v4m10-4v4M4 10h16M6 5h12q2 0 2 2v12q0 2-2 2H6q-2 0-2-2V7q0-2 2-2z",
    "agenda": "M8 3h10q2 0 2 2v14q0 2-2 2H8q-2 0-2-2V5q0-2 2-2zM4 7h4m-4 5h4m-4 5h4m4-8h5m-5 4h5",
    "check": "m4.5 12.5 5 5 10-11",
    "edit": "m14 5 5 5M4 20l5-1 11-11q1-1 0-2l-3-3q-1-1-2 0L4.5 15z",
    "reopen": "M5 4v6h6M5 10a8 8 0 1 1-.2 6",
    "clock": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 6v6l4 2",
    "close": "m6 6 12 12M18 6 6 18",
}


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    result = QFont(QApplication.font())
    result.setPixelSize(size)
    result.setWeight(weight)
    return result


@lru_cache(maxsize=80)
def icon(name: str, colour: str = MUTED, size: int = 18) -> QIcon:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<path d="{PATHS[name]}" fill="none" stroke="{colour}" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def label(text: str, role: str = "") -> QLabel:
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setProperty("role", role)
    return item


def button(text: str, role: str = "", glyph: str = "") -> QPushButton:
    item = QPushButton(text)
    item.setProperty("role", role)
    item.setAutoDefault(False)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        item.setIcon(icon(glyph, "#FFFFFF" if role == "primary" else MUTED))
    return item


def glass_panel() -> QFrame:
    panel = QFrame()
    panel.setObjectName("glass")
    shadow = QGraphicsDropShadowEffect(panel)
    shadow.setColor(QColor(38, 51, 60, 24))
    shadow.setBlurRadius(15)
    shadow.setOffset(0, 3)
    panel.setGraphicsEffect(shadow)
    return panel


def paint_frost(widget: QWidget, native: bool) -> None:
    painter = QPainter(widget)
    rect = QRectF(widget.rect())
    wash = QLinearGradient(rect.topLeft(), rect.bottomRight())
    wash.setColorAt(0, QColor(216, 232, 236, 148 if native else 255))
    wash.setColorAt(.50, QColor(236, 239, 244, 155 if native else 255))
    wash.setColorAt(1, QColor(236, 225, 220, 142 if native else 255))
    # Replace the backing pixels on every repaint so the tint does not build up.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(rect, wash)


class Backdrop(QWidget):
    def __init__(self, native: bool):
        super().__init__()
        self.native = native

    def paintEvent(self, _event) -> None:
        paint_frost(self, self.native)


def motion_enabled() -> bool:
    if sys.platform == "win32":
        enabled = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    return False


def animate_property(owner, target, name: bytes, start: float, end: float) -> None:
    previous = getattr(owner, "_animation", None)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
        owner._animation = None
    if not motion_enabled():
        target.setProperty(name.decode(), end)
        return
    animation = QPropertyAnimation(target, name, owner)
    owner._animation = animation
    animation.setDuration(135)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.start()


def apply_theme(app: QApplication) -> None:
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    font_id = QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / "SourceSans3VF-Upright.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    base = QFont(families[0] if families else "Segoe UI Variable Text")
    base.setPixelSize(14)
    app.setFont(base)
    QDir.setSearchPaths("planner", [str(ASSETS)])
    app.setWindowIcon(QIcon(str(ASSETS / "calendar.svg")))
    app.setStyleSheet((ASSETS / "theme.qss").read_text(encoding="utf-8"))
