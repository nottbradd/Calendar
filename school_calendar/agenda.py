from __future__ import annotations

from datetime import date
from itertools import groupby

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .appearance import ACCENT, INK, MUTED, font, label
from .model import Entry, NEUTRAL_COLOUR, countdown, full_day


class EntryRow(QAbstractButton):
    def __init__(self, entry: Entry, colour: str, today: date):
        super().__init__()
        self.entry = entry
        self.colour = colour
        description = f"{entry.title} · {entry.subject or 'No subject'} · {entry.type_label} · {full_day(entry.day)} · {entry.time_label} · {countdown(entry, today)}"
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(49)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = self.rect().adjusted(1, 1, -1, -1)
        if self.hasFocus() or self.underMouse() or self.isDown():
            painter.setBrush(QColor(216, 231, 233, 170))
            painter.setPen(QPen(QColor(ACCENT), 2) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(box, 6, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.colour))
        painter.drawRoundedRect(QRect(5, 9, 3, 31), 1.5, 1.5)
        text_left = 16
        title_box = QRect(text_left, 2, self.width() - text_left - 6, 23)
        painter.setPen(QColor(MUTED if self.entry.completed else INK))
        painter.setFont(font(16, QFont.Weight.DemiBold))
        title = ("✓ " if self.entry.completed else "") + self.entry.title
        painter.drawText(title_box, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, title_box.width()))
        painter.setFont(font(14))
        painter.setPen(QColor(MUTED))
        time_box = QRect(self.width() - 52, 24, 47, 19)
        painter.drawText(time_box, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.entry.time_label)
        meta_box = QRect(text_left, 28, max(1, time_box.x() - text_left - 5), 19)
        meta = " · ".join(filter(None, (self.entry.subject, self.entry.type_label)))
        painter.drawText(meta_box, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, meta_box.width()))


def status_badge(text: str, tone: str) -> QLabel:
    colours = {
        "overdue": ("#80432F", "#F5E5DD"), "today": ("#2F5760", "#DAE9E9"),
        "done": ("#355C51", "#DEEAE4"), "later": (MUTED, "transparent"),
    }
    fg, bg = colours[tone]
    badge = label(text)
    badge.setStyleSheet(f"color:{fg}; background:{bg}; border-radius:5px; padding:1px 5px; font-size:12px; font-weight:600;")
    return badge


def tone_for(entry: Entry, today: date) -> str:
    if entry.completed:
        return "done"
    return "overdue" if entry.day < today else "today" if entry.day == today else "later"


class EntryList(QScrollArea):
    open_entry = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewport().setAutoFillBackground(False)
        self.setMinimumWidth(100)

    def display(self, entries, colours, today, *, groups=True, empty_title="", empty_body="", reset=False):
        scroll = 0 if reset else self.verticalScrollBar().value()
        contents = QWidget()
        contents.setObjectName("listContents")
        contents.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(contents)
        layout.setContentsMargins(1, 0, 4, 2)
        layout.setSpacing(3)
        if not entries:
            layout.addSpacing(14)
            header = label(empty_title, "subheading")
            header.setWordWrap(True)
            layout.addWidget(header)
            hint = label(empty_body, "muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        for group_day, items in groupby(entries, key=lambda e: e.day):
            day_entries = list(items)
            if groups:
                if layout.count():
                    layout.addSpacing(5)
                heading = QHBoxLayout()
                heading.setContentsMargins(1, 3, 1, 3)
                date_text = f"{group_day:%a}, {group_day.day} {group_day:%b}"
                if group_day.year != today.year:
                    date_text += f" {group_day.year}"
                day_label = label(date_text, "field")
                heading.addWidget(day_label)
                heading.addStretch()
                first = day_entries[0]
                if not first.completed:
                    heading.addWidget(status_badge(countdown(first, today), tone_for(first, today)))
                layout.addLayout(heading)
            for entry in day_entries:
                row = EntryRow(entry, colours.get(entry.subject, NEUTRAL_COLOUR), today)
                row.clicked.connect(lambda checked=False, item=entry: self.open_entry.emit(item))
                layout.addWidget(row)
        layout.addStretch(1)
        self.setWidget(contents)
        contents.setAutoFillBackground(False)
        self.verticalScrollBar().setValue(scroll)
