from __future__ import annotations

from collections import defaultdict
from datetime import date

from PySide6.QtCore import Property, QAbstractTableModel, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QStyle, QTableView

from .appearance import ACCENT, INK, MUTED, animate_property, font
from .model import Entry, NEUTRAL_COLOUR, WEEKDAYS, full_day, month_grid
from .storage import Snapshot


class MonthModel(QAbstractTableModel):
    def __init__(self, selected: date, parent=None):
        super().__init__(parent)
        self.weeks = month_grid(selected)
        self.by_day: dict[date, list[Entry]] = {}
        self.colours: dict[str, str] = {}

    def show_month(self, selected: date, snapshot: Snapshot) -> None:
        self.beginResetModel()
        self.weeks = month_grid(selected)
        self.by_day = defaultdict(list)
        for entry in snapshot.entries:
            self.by_day[entry.day].append(entry)
        self.colours = snapshot.colours
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.weeks)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 7

    def day_at(self, index):
        if index.isValid():
            return self.weeks[index.row()][index.column()]
        return None

    def index_of(self, day):
        for row, week in enumerate(self.weeks):
            if day in week:
                return self.index(row, week.index(day))
        return QModelIndex()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        day = self.day_at(index)
        if day is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(day.day)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            lines = [full_day(day)]
            for entry in self.by_day.get(day, ()):
                text = f"{entry.time_label} · {entry.title} · {entry.subject or entry.type_label}"
                lines.append(text + (" · Completed" if entry.completed else ""))
            return "\n".join(lines)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return WEEKDAYS[section]
        return None


def chip_layout(rect: QRect, entries: list[Entry]) -> tuple[list[tuple[QRect, Entry]], QRect | None]:
    """Painting and hit testing share the same boxes, including the compact count."""
    if not entries:
        return [], None
    x = rect.x() + 4
    y = rect.y() + (28 if rect.height() >= 64 else 24)
    width = max(1, rect.width() - 8)
    room = rect.bottom() - y - 2
    if room < 15:
        return [], QRect(rect.right() - 28, rect.top() + 3, 25, 16)
    height = 32 if room >= 32 and (len(entries) == 1 or room >= 50) else 18
    count = min(2, len(entries), (room + 2) // (height + 2))
    if count < len(entries):
        count = max(0, min(count, (room - 15) // (height + 2)))
    boxes = [(QRect(x, y + i * (height + 2), width, height), entry) for i, entry in enumerate(entries[:count])]
    more = QRect(x, y + count * (height + 2), width, 15) if count < len(entries) else None
    return boxes, more


class DayDelegate(QStyledItemDelegate):
    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def paint(self, painter: QPainter, option, index):
        model = self.grid.month_model
        day = model.day_at(index)
        rect = option.rect
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = day is not None and day == self.grid.selected
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        in_month = day and day.month == self.grid.selected.month
        if not in_month:
            background = QColor(112, 135, 149, 12)
        elif index.column() >= 5:
            background = QColor(154, 176, 182, 12)
        else:
            background = QColor(255, 255, 255, 35)
        painter.fillRect(rect, QColor(145, 175, 183, 25) if hovered else background)
        painter.setPen(QColor(87, 110, 122, 36))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())
        if not day:
            painter.restore()
            return
        if selected:
            selected_wash = QColor("#C2DADB")
            selected_wash.setAlphaF(.15 + .24 * self.grid.selectionStrength)
            painter.setBrush(selected_wash)
            painter.setPen(QPen(QColor(ACCENT if self.grid.hasFocus() else "#809EA5"), 2 if self.grid.hasFocus() else 1))
            painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 6, 6)

        today = day == self.grid.today
        badge = QRect(rect.x() + 5, rect.y() + 3, 23, 20)
        if today:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(badge, 9, 9)
        number_colour = "#FFFFFF" if today else (INK if in_month else "#657781")
        painter.setPen(QColor(number_colour))
        painter.setFont(font(14, QFont.Weight.DemiBold if today or selected else QFont.Weight.Normal))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(day.day))
        entries = model.by_day.get(day, [])
        boxes, more = chip_layout(rect, entries)
        count_in_header = more is not None and more.top() < rect.y() + 21
        if today and rect.width() >= (99 if count_in_header else 65):
            painter.setFont(font(12, QFont.Weight.DemiBold))
            painter.setPen(QColor(ACCENT))
            painter.drawText(badge.translated(26, 0).adjusted(0, 0, 16, 0), Qt.AlignmentFlag.AlignVCenter, "Today")

        for box, entry in boxes:
            self.paint_chip(painter, box, entry, model.colours.get(entry.subject, NEUTRAL_COLOUR))
        if more is not None:
            painter.setPen(QColor(MUTED))
            painter.setFont(font(12, QFont.Weight.DemiBold))
            count = len(entries) - len(boxes)
            text = f"+{count}" if count_in_header or more.width() < 42 else f"+{count} more"
            painter.drawText(more.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    @staticmethod
    def paint_chip(painter, rect: QRect, entry: Entry, colour: str) -> None:
        accent = QColor(colour)
        tint = QColor(accent)
        tint.setAlpha(16 if entry.completed else 27)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRect(rect.x() + 2, rect.y() + 3, 2, rect.height() - 6), 1, 1)
        text_box = rect.adjusted(8, 0, -3, 0)
        tall = rect.height() > 24
        title = ("✓ " if entry.completed else "") + entry.title
        if not tall and entry.subject:
            title += " · " + entry.subject
        painter.setFont(font(12, QFont.Weight.DemiBold))
        painter.setPen(QColor(MUTED if entry.completed else INK))
        first = QRect(text_box.x(), text_box.y(), text_box.width(), 17) if tall else text_box
        painter.drawText(first, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, first.width()))
        if tall:
            painter.setFont(font(12))
            painter.setPen(QColor(MUTED))
            second = text_box.adjusted(0, 15, 0, -1)
            text = painter.fontMetrics().elidedText(entry.subject or entry.type_label, Qt.TextElideMode.ElideRight, second.width())
            painter.drawText(second, Qt.AlignmentFlag.AlignVCenter, text)


class MonthView(QTableView):
    choose = Signal(object)
    add = Signal(object)
    details = Signal(object)
    show_day = Signal(object)

    def __init__(self, selected: date):
        super().__init__()
        self.setObjectName("monthGrid")
        self.setAccessibleName("Month calendar")
        self.setAccessibleDescription("Use arrow keys to select a date, Enter to view its entries, or Space to add an entry.")
        self.selected = selected
        self.today = selected
        self._strength = 1.0
        self._snapshot = None
        self.month_model = MonthModel(selected, self)
        self.setModel(self.month_model)
        self.setItemDelegate(DayDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.setShowGrid(False)
        self.viewport().setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalHeader().hide()
        self.verticalHeader().setMinimumSectionSize(20)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setMinimumSectionSize(20)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setFixedHeight(30)
        self.horizontalHeader().setSectionsClickable(False)
        self._press_day = None

    def display(self, selected: date, today: date, snapshot: Snapshot) -> None:
        previous = self.selected
        self.selected, self.today = selected, today
        if snapshot is not self._snapshot or selected.replace(day=1) != previous.replace(day=1):
            self.month_model.show_month(selected, snapshot)
            self._snapshot = snapshot
        self.setCurrentIndex(self.month_model.index_of(selected))
        if selected != previous:
            animate_property(self, self, b"selectionStrength", 0.0, 1.0)
        self.viewport().update()

    @Property(float)
    def selectionStrength(self):
        return self._strength

    @selectionStrength.setter
    def selectionStrength(self, value):
        self._strength = value
        self.viewport().update()

    def mousePressEvent(self, event):
        self._press_day = self.month_model.day_at(self.indexAt(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        point = event.position().toPoint()
        index = self.indexAt(point)
        day = self.month_model.day_at(index)
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or not day or day != self._press_day:
            return
        boxes, more = chip_layout(self.visualRect(index), self.month_model.by_day.get(day, []))
        self.choose.emit(day)
        for rect, entry in boxes:
            if rect.contains(point):
                self.details.emit(entry)
                return
        if more is not None and more.contains(point):
            self.show_day.emit(day)
        else:
            self.add.emit(day)

    def keyPressEvent(self, event):
        steps = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -7, Qt.Key.Key_Down: 7}
        key = event.key()
        if key in steps:
            ordinal = max(1, min(date.max.toordinal(), self.selected.toordinal() + steps[key]))
            self.choose.emit(date.fromordinal(ordinal))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.show_day.emit(self.selected)
        elif key == Qt.Key.Key_Space:
            self.add.emit(self.selected)
        else:
            super().keyPressEvent(event)
