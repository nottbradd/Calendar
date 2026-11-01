from __future__ import annotations

from bisect import bisect_right
from datetime import date, time

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .appearance import ACCENT, INK, MUTED, button, font, label
from .model import Entry, NEUTRAL_COLOUR, full_day


GUTTER, TOP, HIT_HEIGHT = 45, 12, 35
DAY_HEADER, ALL_DAY_ROW = 31, 23
CENTER = Qt.AlignmentFlag.AlignCenter
MIDDLE = Qt.AlignmentFlag.AlignVCenter


def week_days(selected: date) -> list[date | None]:
    monday = selected.toordinal() - selected.weekday()
    return [date.fromordinal(n) if n <= date.max.toordinal() else None for n in range(monday, monday + 7)]


def week_title(selected: date) -> str:
    days = [d for d in week_days(selected) if d is not None]
    first, last = days[0], days[-1]
    start = str(first.day)
    if first.month != last.month:
        start += f" {first:%b}"
    if first.year != last.year:
        start += f" {first.year}"
    return f"{start}–{last.day} {last:%b} {last.year}"


def minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def lanes(buttons, y):
    """Place colliding click targets side by side without stretching the painted durations."""
    result, cluster, bottoms = [], [], []
    for item in buttons:  # Snapshot order is chronological within each day.
        top = y(minutes(item.entry.at))
        end = y(minutes(item.entry.end_at)) if item.entry.end_at else top
        if bottoms and top >= max(bottoms):
            result.extend((event, lane, len(bottoms)) for event, lane in cluster)
            cluster, bottoms = [], []
        lane = next((i for i, bottom in enumerate(bottoms) if bottom <= top), len(bottoms))
        if lane == len(bottoms):
            bottoms.append(0)
        bottoms[lane] = max(end, top + HIT_HEIGHT)
        cluster.append((item, lane))
    result.extend((event, lane, len(bottoms)) for event, lane in cluster)
    return result


class WeekEntry(QAbstractButton):
    def __init__(self, entry: Entry, colour, parent):
        super().__init__(parent)
        self.entry, self.colour = entry, QColor(colour)
        self.duration_height = 0
        description = " · ".join(filter(None, (entry.title, entry.subject, full_day(entry.day),
                                                entry.time_description, "Completed" if entry.completed else "")))
        self.setToolTip(description)
        self.setAccessibleName(description)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        e = self.entry
        point = e.at is not None and e.end_at is None
        fill = QColor(self.colour)
        fill.setAlpha(18 if e.completed else 42)
        if self.underMouse():
            fill.setAlpha(fill.alpha() + 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        height = self.height() if e.at is None else self.duration_height
        if not point:
            box = QRectF(0, 0, self.width(), max(1, height))
            p.drawRoundedRect(box, 4, min(4, height / 2))
            p.fillRect(QRectF(0, 0, 2.5, box.height()), self.colour)
        else:
            # A hollow marker has an instant on the axis and no vertical duration.
            p.setPen(QPen(self.colour, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(1, 1, 7, 7))
        left = 12 if point else 6
        text = QRect(left, 0, max(1, self.width() - left - 3), self.height())
        title = ("✓ " if e.completed else "") + e.title
        p.setPen(QColor(MUTED if e.completed else INK))
        p.setFont(font(13, QFont.Weight.DemiBold))
        if e.at is not None:
            title_box = text.adjusted(0, 2, 0, -16)
            metrics = p.fontMetrics()
            lines = [title]
            if title_box.height() >= 2 * metrics.height() and metrics.horizontalAdvance(title) > text.width():
                words = title.split()
                split = 1
                while split < len(words) and metrics.horizontalAdvance(" ".join(words[:split + 1])) <= text.width():
                    split += 1
                lines = [" ".join(words[:split]), " ".join(words[split:])]
            for i, line in enumerate(lines):
                p.drawText(title_box.translated(0, i * metrics.height()), Qt.AlignmentFlag.AlignTop,
                           metrics.elidedText(line, Qt.TextElideMode.ElideRight, text.width()))
            p.setFont(font(12))
            p.setPen(QColor(MUTED))
            caption = QRect(text.x(), self.height() - 17, text.width(), 16)
            p.drawText(caption, MIDDLE, p.fontMetrics().elidedText(e.time_label, Qt.TextElideMode.ElideRight, text.width()))
        else:
            p.drawText(text, MIDDLE, p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, text.width()))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(ACCENT), 1.5))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)


class ScheduleSurface(QWidget):
    def __init__(self, week, *, header=False):
        super().__init__()
        self.week, self.header = week, header
        self.setAccessibleName("Week dates and all-day entries" if header else "Hourly schedule")

    def paintEvent(self, event):
        w = self.week
        if not w.days:
            return
        p = QPainter(self)
        p.setFont(font(12))
        for i, day in enumerate(w.days):
            left, right = w.edges[i:i + 2]
            area = QRect(left, 0, right - left, self.height())
            if day == w.today:
                p.fillRect(area, QColor(92, 149, 153, 19))
            elif i >= 5:
                p.fillRect(area, QColor(124, 151, 169, 11))
            p.setPen(QColor(82, 109, 122, 38))
            p.drawLine(left, 0, left, self.height())
        if self.header:
            p.drawLine(0, DAY_HEADER, self.width(), DAY_HEADER)
            p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        for hour in range(25):
            y = w.y(hour * 60)
            if y < event.rect().top() - 10 or y > event.rect().bottom() + 10:
                continue
            p.setPen(QColor(82, 108, 119, 53))
            p.drawLine(0, y, self.width(), y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        w = self.week
        column = bisect_right(w.edges, event.position().x()) - 1
        if not 0 <= column < len(w.days) or not w.days[column]:
            return
        at = None
        if not self.header:
            if not w.y(0) <= event.position().y() < w.y(1440):
                return
            minute = (event.position().y() - TOP + .5) * 60 / w.hour_height
            slot = min(1425, max(0, int(minute // 15) * 15))
            at = time(*divmod(slot, 60))
        w.add.emit(w.days[column], at)


class TimeAxis(QWidget):
    def __init__(self, week):
        super().__init__()
        self.week = week
        self.setFixedWidth(GUTTER)
        self.setAccessibleName("Hours in local time")

    def paintEvent(self, _event):
        scroll = self.week.scroll
        p = QPainter(self)
        p.setClipRect(0, 0, self.width(), scroll.viewport().height())
        p.setFont(font(12))
        p.setPen(QColor(MUTED))
        for hour in range(25):
            y = self.week.y(hour * 60) - scroll.verticalScrollBar().value()
            p.drawText(QRect(0, y - 9, GUTTER - 6, 18), Qt.AlignmentFlag.AlignRight | MIDDLE, f"{hour:02}:00")

    def wheelEvent(self, event):
        QApplication.sendEvent(self.week.scroll.viewport(), event)


class WeekView(QWidget):
    add = Signal(object, object)
    details = Signal(object)
    show_day = Signal(object)

    def __init__(self):
        super().__init__()
        self.days, self.edges = [], [0] * 8
        self.timed, self.all_day = [], []
        self.headings, self.more = [], []
        self.today = self.selected = self.snapshot = None
        self.hour_height = 46
        self.lane_width = QFontMetrics(font(12)).horizontalAdvance("00:00–00:00") + 17
        self.reset_scroll = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header_clip = QWidget()
        self.header = ScheduleSurface(self, header=True)
        self.header.setParent(self.header_clip)
        header_row = QHBoxLayout()
        header_row.setSpacing(0)
        band_label = label("All day", "caption")
        band_label.setContentsMargins(0, DAY_HEADER, 3, 0)
        band_label.setAlignment(CENTER)
        band_label.setFixedWidth(GUTTER)
        header_row.addWidget(band_label)
        header_row.addWidget(self.header_clip, 1)
        layout.addLayout(header_row)
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("Weekly schedule, midnight to midnight")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.viewport().setAutoFillBackground(False)
        self.canvas = ScheduleSurface(self)
        self.scroll.setWidget(self.canvas)
        self.canvas.setAutoFillBackground(False)
        schedule_row = QHBoxLayout()
        schedule_row.setSpacing(0)
        self.axis = TimeAxis(self)
        schedule_row.addWidget(self.axis)
        schedule_row.addWidget(self.scroll, 1)
        layout.addLayout(schedule_row, 1)
        self.reflow_timer = QTimer(self, singleShot=True, timeout=self.reflow)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.horizontalScrollBar().valueChanged.connect(lambda x: self.header.move(-x, 0))
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _y: self.axis.update())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.reflow_timer.start(0)
        return super().eventFilter(obj, event)

    def y(self, minute):
        return TOP + round(minute * self.hour_height / 60)

    def display(self, selected, today, snapshot):
        days = week_days(selected)
        if days != self.days or snapshot != self.snapshot or today != self.today:
            self.reset_scroll |= days != self.days
            self.days, self.today, self.snapshot = days, today, snapshot
            self._build_week()
        self.selected = selected
        for day, heading in zip(self.days, self.headings):
            heading.setProperty("selected", day == selected)
            heading.style().polish(heading)
        self.reflow()

    def _build_week(self):
        for parent in (self.header, self.canvas):
            for child in parent.findChildren(QAbstractButton, options=Qt.FindChildOption.FindDirectChildrenOnly):
                child.hide()
                child.deleteLater()
        self.headings, self.timed, self.all_day, self.more = [], [], [], []
        by_day = {day: [] for day in self.days if day is not None}
        for entry in self.snapshot.entries:
            if entry.day in by_day:
                by_day[entry.day].append(entry)
        for day in self.days:
            heading = button(f"{day:%a} {day.day}" if day else "", "weekday")
            heading.setParent(self.header)
            heading.setEnabled(day is not None)
            if day:
                heading.setAccessibleName(("Today · " if day == self.today else "") + full_day(day))
                heading.setToolTip(f"Add an entry for {heading.accessibleName()}")
                heading.clicked.connect(lambda checked=False, d=day: self.add.emit(d, None))
            heading.setProperty("today", day == self.today)
            heading.show()
            self.headings.append(heading)
            timed, all_day = [], []
            items = by_day.get(day, ())
            untimed = sum(e.at is None for e in items)
            for entry in items:
                if entry.at is None and len(all_day) >= (1 if untimed > 2 else 2):
                    continue
                parent = self.header if entry.at is None else self.canvas
                widget = WeekEntry(entry, self.snapshot.colours.get(entry.subject, NEUTRAL_COLOUR), parent)
                widget.clicked.connect(lambda checked=False, e=entry: self.details.emit(e))
                widget.show()
                (all_day if entry.at is None else timed).append(widget)
            overflow = None
            if untimed > len(all_day):
                overflow = button(f"+{untimed - len(all_day)} more", "more")
                overflow.setParent(self.header)
                overflow.setAccessibleName(f"Show all entries for {full_day(day)}")
                overflow.clicked.connect(lambda checked=False, d=day: self.show_day.emit(d))
                overflow.show()
            self.timed.append(timed)
            self.all_day.append(all_day)
            self.more.append(overflow)
        rows = max(1, max(len(items) + bool(more) for items, more in zip(self.all_day, self.more)))
        self.header_clip.setFixedHeight(DAY_HEADER + 5 + rows * ALL_DAY_ROW)

    def reflow(self):
        if not self.days or not self.isVisible():
            return
        viewport = self.scroll.viewport()
        bar = self.scroll.verticalScrollBar()
        top_hour = 9 if self.reset_scroll else bar.value() / self.hour_height
        self.reset_scroll = False
        self.hour_height = max(39, (viewport.height() - 2 * TOP) / 12)
        placed = [lanes(items, self.y) for items in self.timed]
        # Share spare width equally. Only unusually busy columns need extra width.
        widths = [max(60, max((count * self.lane_width for _, _, count in day), default=0)) for day in placed]
        available = max(sum(widths), viewport.width())
        for n, i in enumerate(sorted(range(7), key=widths.__getitem__, reverse=True)):
            widths[i] = max(widths[i], available // (7 - n))
            available -= widths[i]
        self.edges = [0]
        for width in widths:
            self.edges.append(self.edges[-1] + width)
        self.canvas.resize(self.edges[-1], self.y(1440) + HIT_HEIGHT)
        self.header.resize(self.edges[-1], self.header_clip.height())
        for i, items in enumerate(placed):
            x, width = self.edges[i], widths[i]
            self.headings[i].setGeometry(x + 2, 1, width - 4, DAY_HEADER - 3)
            band = self.all_day[i] + ([self.more[i]] if self.more[i] else [])
            for j, widget in enumerate(band):
                widget.setGeometry(x + 3, DAY_HEADER + 3 + j * ALL_DAY_ROW, width - 6, ALL_DAY_ROW - 2)
            for widget, lane, count in items:
                top = self.y(minutes(widget.entry.at))
                duration = self.y(minutes(widget.entry.end_at)) - top if widget.entry.end_at else 0
                widget.duration_height = duration
                left = x + width * lane // count
                right = x + width * (lane + 1) // count
                widget.setGeometry(left + 3, top, right - left - 5, max(duration, HIT_HEIGHT))
        bar.setSingleStep(max(12, round(self.hour_height / 2)))
        bar.setValue(round(top_hour * self.hour_height))
        self.header.update()
        self.canvas.update()
        self.axis.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.reflow_timer.start(0)
