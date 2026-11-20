from __future__ import annotations

import ctypes
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QDir, QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsScene, QLabel, QPushButton, QWidget,
)

from .windows import transparency_allowed


PAPER = "#F0EEEB"
FROST = "#FCFAF8"
MIST = "#E2DFDA"
STONE = "#BEB9B3"
RULE = "#D6D2CD"
INK = "#26232A"
MUTED = "#59535A"
ACCENT = "#713138"
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
    "settings": "M4 7h5m4 0h7M4 17h9m4 0h3M13 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0z",
}


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    result = QFont(QApplication.font())
    result.setPixelSize(size)
    result.setWeight(weight)
    return result


@lru_cache(maxsize=80)
def icon(name: str, colour: str = INK, size: int = 18) -> QIcon:
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
    if role in ("month", "heading", "subheading"):
        face = item.font()
        face.setFamilies(["Manrope", *QApplication.font().families()])
        item.setFont(face)
    return item


@lru_cache(maxsize=64)
def subject_icon(colour: str, size=14) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setWindow(0, 0, 28, 28)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(3, 3, 22, 22)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    result = QIcon(pixmap)
    checked = pixmap.copy()
    painter = QPainter(checked)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 28, size / 28)
    painter.setPen(QPen(QColor("white"), 2))
    painter.drawLine(8, 14, 12, 18)
    painter.drawLine(12, 18, 21, 9)
    painter.end()
    result.addPixmap(checked, QIcon.Mode.Normal, QIcon.State.On)
    return result


def button(text: str, role: str = "", glyph: str = "") -> QPushButton:
    item = QPushButton(text)
    item.setProperty("role", role)
    item.setAutoDefault(False)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        item.setIcon(icon(glyph, FROST if role == "primary" else INK))
    return item


def blur_backdrop(source: QPixmap, radius: int) -> QPixmap:
    """Blur only the backing image. All foreground widgets paint at full resolution."""
    if source.isNull():
        return QPixmap()
    size = source.deviceIndependentSize().toSize() / 2
    small = source.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    small.setDevicePixelRatio(1)
    scene = QGraphicsScene()
    item = scene.addPixmap(small)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius / 2)
    item.setGraphicsEffect(effect)
    result = QPixmap(small.size())
    result.fill(QColor(PAPER))
    painter = QPainter(result)
    bounds = QRectF(result.rect())
    scene.render(painter, bounds, bounds)
    painter.end()
    return result


@lru_cache(maxsize=1)
def workspace_images() -> tuple[QPixmap, QPixmap]:
    paper = QPixmap(str(ASSETS / "ambient-frost.png"))
    return paper, blur_backdrop(paper, 26)


def paint_glass(painter: QPainter, rect: QRectF, draw_backdrop=None, *, tint=135, radius=12):
    edge = rect.adjusted(.5, .5, -.5, -.5)
    shape = QPainterPath()
    shape.addRoundedRect(edge, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(shape)
    if draw_backdrop:
        draw_backdrop(painter)
    wash = QColor(FROST)
    wash.setAlpha(tint if draw_backdrop else 255)
    painter.fillPath(shape, wash)
    painter.setPen(QPen(QColor(255, 254, 253, 225), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shape)
    painter.restore()


class GlassPanel(QFrame):
    def __init__(self, workspace, *, tint):
        super().__init__(workspace)
        self.workspace, self.tint = workspace, tint
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setColor(QColor(49, 39, 38, 27))
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

    def paintEvent(self, _event):
        def backdrop(p):
            p.save()
            pos = self.mapTo(self.workspace, QPoint())
            p.translate(-pos)
            self.workspace.paint_paper(p, blurred=True)
            p.restore()

        p = QPainter(self)
        paint_glass(p, QRectF(self.rect()), backdrop if self.workspace.glass else None, tint=self.tint)


def paint_frost(widget: QWidget, native: bool) -> None:
    painter = QPainter(widget)
    wash = QColor(FROST)
    wash.setAlpha(221 if native else 255)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(widget.rect(), wash)


class Backdrop(QWidget):
    def __init__(self, native: bool):
        super().__init__()
        self.native = native
        self.glass = transparency_allowed() and not workspace_images()[1].isNull()

    def paint_paper(self, painter, *, blurred=False):
        bounds = QRectF(self.rect())
        paper = QColor(PAPER)
        paper.setAlpha(225 if self.native and self.glass and not blurred else 255)
        painter.fillRect(bounds, paper)
        if not self.glass:
            return
        texture = workspace_images()[int(blurred)]
        scale = max(bounds.width() / texture.width(), bounds.height() / texture.height())
        width, height = bounds.width() / scale, bounds.height() / scale
        crop = QRectF((texture.width() - width) / 2, (texture.height() - height) / 2, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(.8)
        painter.drawPixmap(bounds, texture, crop)
        painter.restore()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        self.paint_paper(painter)


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
    for filename in ("Geist-Variable.ttf", "Manrope-Variable.ttf", "SourceSans3VF-Upright.ttf"):
        QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / filename))
    base = QFont()
    base.setFamilies(["Geist", "Source Sans 3", "Segoe UI"])
    base.setPixelSize(14)
    base.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias)
    base.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    base.setFeature(QFont.Tag("tnum"), 1)
    app.setFont(base)
    palette = app.palette()
    role = QPalette.ColorRole
    for key, colour in {
        role.Window: PAPER, role.Base: FROST, role.AlternateBase: PAPER, role.Button: FROST,
        role.WindowText: INK, role.Text: INK, role.ButtonText: INK, role.PlaceholderText: MUTED,
        role.Highlight: ACCENT, role.HighlightedText: FROST, role.Accent: ACCENT,
        role.Link: ACCENT, role.Light: FROST, role.Midlight: MIST, role.Mid: STONE, role.Dark: MUTED,
    }.items():
        palette.setColor(key, QColor(colour))
    for key in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, key, QColor("#716B6D"))
    app.setPalette(palette)
    QDir.setSearchPaths("planner", [str(ASSETS)])
    app.setWindowIcon(QIcon(str(ASSETS / "calendar.svg")))
    app.setStyleSheet((ASSETS / "theme.qss").read_text(encoding="utf-8"))
