from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout

from . import APP_NAME
from .agenda import EntryList
from .appearance import Backdrop, button, glass_panel, label, motion_enabled
from .dialogs import DayDetails, EntryDetails, EntryEditor
from .model import Entry, NEUTRAL_COLOUR, shift_month
from .month_view import MonthView
from .settings import SettingsDialog
from .storage import Store, StoreError
from .week_view import WeekView, week_days, week_title
from .windows import enable_system_frost


class CalendarWindow(QMainWindow):
    def __init__(self, store: Store, clock=date.today):
        super().__init__()
        self.store, self.clock = store, clock
        self.snapshot = store.read()
        self.today = self.selected = clock()
        self.dialog = None
        self.show_completed = False
        self.compact_agenda_open = False
        self._closing = False
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(760, 516)
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1200, area.width() - 35), min(750, area.height() - 65))
        background = Backdrop(enable_system_frost(self))
        self.setCentralWidget(background)
        outer = QVBoxLayout(background)
        outer.setContentsMargins(16, 10, 16, 13)
        outer.setSpacing(9)
        outer.addLayout(self._toolbar())

        self.read_error = label("", "error")
        self.read_error.setWordWrap(True)
        self.read_error.hide()
        outer.addWidget(self.read_error)
        panels = QHBoxLayout()
        panels.setSpacing(10)
        panels.addWidget(self._calendar_panel(), 7)
        self.agenda_panel = self._agenda_panel()
        panels.addWidget(self.agenda_panel, 3)
        outer.addLayout(panels, 1)
        self.select(self.selected)
        self.render_agenda()
        if self.snapshot.view == "month":
            self.grid.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.week.headings[self.selected.weekday()].setFocus(Qt.FocusReason.OtherFocusReason)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.open_editor(self.selected))
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.go_today)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self.navigate(-1))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self.navigate(1))
        self.day_timer = QTimer(self, timeout=self.check_day)
        self.day_timer.start(30_000)

    def _toolbar(self):
        top = QHBoxLayout()
        top.setSpacing(5)
        self.month_label = label("", "month")
        top.addWidget(self.month_label)
        top.addSpacing(6)
        self.previous = button("", "icon", "previous")
        self.following = button("", "icon", "next")
        self.today_button = button("Today")
        self.previous.setFixedWidth(29)
        self.following.setFixedWidth(29)
        self.today_button.setToolTip("Go to today (Ctrl+T)")
        self.previous.clicked.connect(lambda: self.navigate(-1))
        self.today_button.clicked.connect(self.go_today)
        self.following.clicked.connect(lambda: self.navigate(1))
        for action in (self.previous, self.today_button, self.following):
            top.addWidget(action)
        top.addSpacing(6)
        switch = QFrame()
        switch.setObjectName("viewSwitch")
        switches = QHBoxLayout(switch)
        switches.setContentsMargins(2, 2, 2, 2)
        switches.setSpacing(1)
        self.views = QButtonGroup(self)
        self.month_button = button("Month", "segment")
        self.week_button = button("Week", "segment")
        for tab in (self.month_button, self.week_button):
            tab.setCheckable(True)
            tab.setAccessibleName(tab.text() + " view")
            self.views.addButton(tab)
            switches.addWidget(tab)
        self.month_button.clicked.connect(lambda: self.switch_view("month"))
        self.week_button.clicked.connect(lambda: self.switch_view("week"))
        top.addWidget(switch)
        top.addStretch()
        self.today_text = label("", "muted")
        top.addWidget(self.today_text)
        top.addSpacing(6)
        self.settings_button = button("Settings", "", "settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setToolTip("Settings (Ctrl+,)")
        self.settings_button.clicked.connect(self.open_settings)
        top.addWidget(self.settings_button)
        self.add_button = button("Add entry", "primary", "plus")
        self.add_button.setToolTip("Add an entry for the selected day (Ctrl+N)")
        self.add_button.clicked.connect(lambda: self.open_editor(self.selected))
        top.addWidget(self.add_button)
        return top

    def _calendar_panel(self):
        panel = glass_panel()
        body = QVBoxLayout(panel)
        body.setContentsMargins(8, 5, 8, 5)
        body.setSpacing(3)
        self.grid = MonthView(self.selected)
        self.grid.choose.connect(self.select)
        self.grid.add.connect(self.open_editor)
        self.grid.details.connect(self.open_details)
        self.grid.show_day.connect(self.open_day)
        self.week = WeekView()
        self.week.add.connect(self.open_slot)
        self.week.details.connect(self.open_details)
        self.week.show_day.connect(self.open_day)
        self.calendar_views = QStackedWidget()
        self.calendar_views.addWidget(self.grid)
        self.calendar_views.addWidget(self.week)
        body.addWidget(self.calendar_views, 1)
        footer = QHBoxLayout()
        self.selected_text = label("", "muted")
        footer.addWidget(self.selected_text)
        footer.addStretch()
        self.week_key = label("○  Start only", "caption")
        self.week_key.setToolTip("A hollow marker is an entry with a start time and no end time.")
        footer.addWidget(self.week_key)
        footer.addSpacing(4)
        self.agenda_toggle = button("Show agenda", "link", "agenda")
        self.agenda_toggle.clicked.connect(self.toggle_compact_agenda)
        footer.addWidget(self.agenda_toggle)
        self.day_button = button("View day", "link")
        self.day_button.clicked.connect(lambda: self.open_day(self.selected))
        footer.addWidget(self.day_button)
        body.addLayout(footer)
        return panel

    def _agenda_panel(self):
        panel = glass_panel()
        panel.setMinimumWidth(234)
        panel.setMaximumWidth(420)
        body = QVBoxLayout(panel)
        body.setContentsMargins(12, 7, 10, 9)
        body.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.count = label("0", "count")
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabs = QButtonGroup(self)
        self.upcoming_button = button("Upcoming", "segment")
        self.completed_button = button("Completed", "segment")
        self.upcoming_button.setCheckable(True)
        self.completed_button.setCheckable(True)
        for tab in (self.upcoming_button, self.completed_button):
            self.tabs.addButton(tab)
            row.addWidget(tab)
        self.upcoming_button.setChecked(True)
        self.upcoming_button.clicked.connect(lambda: self.switch_agenda(False))
        self.completed_button.clicked.connect(lambda: self.switch_agenda(True))
        row.addStretch()
        row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(row)
        self.agenda = EntryList()
        self.agenda.setAccessibleName("Agenda entries")
        self.agenda.open_entry.connect(self.open_details)
        body.addWidget(self.agenda, 1)
        self.count_pulse = QVariantAnimation(self, duration=160)
        self.count_pulse.setStartValue(QColor(157, 189, 196, 150))
        self.count_pulse.setEndValue(QColor(180, 201, 207, 68))
        self.count_pulse.valueChanged.connect(lambda c: self.count.setStyleSheet(f"background:rgba{c.getRgb()};"))
        return panel

    def select(self, day):
        self.selected = day
        weekly = self.snapshot.view == "week"
        self.grid.display(day, self.today, self.snapshot)
        self.calendar_views.setCurrentWidget(self.week if weekly else self.grid)
        (self.week_button if weekly else self.month_button).setChecked(True)
        self.week_key.setVisible(weekly)
        if weekly:
            self.week.display(day, self.today, self.snapshot)
        self.month_label.setText(week_title(day) if weekly else f"{day:%B} {day.year}")
        self._toolbar_size()
        self.selected_text.setText(f"Selected  ·  {day:%a}, {day.day} {day:%b}")
        if weekly:
            days = week_days(day)
            self.previous.setEnabled(date.min not in days)
            self.following.setEnabled(date.max not in days)
        else:
            self.previous.setEnabled((day.year, day.month) > (1, 1))
            self.following.setEnabled((day.year, day.month) < (9999, 12))
        for control, action in ((self.previous, "Previous"), (self.following, "Next")):
            name = f"{action} {self.snapshot.view}"
            control.setAccessibleName(name)
            control.setToolTip(name)

    def _toolbar_size(self):
        compact = self.width() < 960
        self.settings_button.setText("" if compact else "Settings")
        day = self.today
        self.today_text.setText(f"{day:%a}, {day.day} {day:%b}" if compact else f"{day:%A}, {day.day} {day:%B}")
        if hasattr(self, "agenda_panel"):
            compact_week = self.width() < 1000 and self.snapshot.view == "week"
            self.agenda_toggle.setVisible(compact_week)
            self.agenda_toggle.setText("Hide agenda" if self.compact_agenda_open else "Show agenda")
            self.agenda_panel.setVisible(not compact_week or self.compact_agenda_open)

    def toggle_compact_agenda(self):
        self.compact_agenda_open = not self.compact_agenda_open
        self._toolbar_size()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "settings_button"):
            self._toolbar_size()

    def switch_view(self, view):
        if view == self.snapshot.view:
            return
        try:
            self.store.set_view(view)
        except StoreError as exc:
            self.read_error.setText(str(exc))
            self.read_error.show()
            self.select(self.selected)
            return
        self.reload()

    def navigate(self, delta):
        if self.snapshot.view == "week":
            ordinal = min(date.max.toordinal(), max(1, self.selected.toordinal() + delta * 7))
            self.select(date.fromordinal(ordinal))
        else:
            self.select(shift_month(self.selected, delta))

    def go_today(self):
        self.check_day()
        self.select(self.today)

    def check_day(self):
        today = self.clock()
        if self.today != today:
            self.today = today
            self.select(self.selected)
            self.render_agenda()

    def switch_agenda(self, completed):
        self.show_completed = completed
        self.render_agenda(reset=True)

    def render_agenda(self, reset=False):
        entries = [entry for entry in self.snapshot.entries if entry.completed == self.show_completed]
        if self.show_completed:
            empty_title, body = "No completed work", "Entries you mark as complete will appear here."
        elif self.snapshot.entries:
            empty_title, body = "You’re all caught up", "Find your finished work in the Completed tab."
        else:
            empty_title, body = "Nothing scheduled", "Choose a date or use Add entry to plan your first assessment."
        self.count.setText(str(len(entries)))
        self.count.setAccessibleName(f"{len(entries)} {'completed' if self.show_completed else 'open'} entries")
        self.agenda.display(entries, self.snapshot.colours, self.today, empty_title=empty_title, empty_body=body, reset=reset)

    def reload(self):
        if self._closing:
            return
        try:
            current = self.store.read()
        except StoreError as exc:
            self.read_error.setText(f"{exc} Showing the last calendar that could be read. Check {self.store.path}.")
            self.read_error.show()
            return
        self.read_error.hide()
        if current != self.snapshot:
            self.snapshot = current
            self.select(self.selected)
            self.render_agenda()

    def _present(self, dialog):
        self.dialog = dialog
        dialog.finished.connect(lambda _code: self._dismissed(dialog))
        dialog.open()

    def _dismissed(self, dialog):
        if self.dialog is dialog:
            self.dialog = None
        self.reload()

    def open_editor(self, day, entry=None):
        editor = EntryEditor(self, self.store, entry or Entry("", day), self.snapshot.colours)
        editor.saved.connect(self.select)
        self._present(editor)

    def open_slot(self, day, at):
        self.select(day)
        self.open_editor(day, Entry("", day, at=at))

    def open_settings(self):
        dialog = SettingsDialog(self, self.store, self.snapshot.colours)
        dialog.subjects_changed.connect(self.reload)
        self._present(dialog)

    def open_details(self, entry):
        self.select(entry.day)
        details = EntryDetails(self, self.store, entry, self.snapshot.colours.get(entry.subject, NEUTRAL_COLOUR), self.today)
        details.edit.connect(lambda item: self.open_editor(item.day, item))
        details.changed.connect(self._entry_changed)
        self._present(details)

    def _entry_changed(self):
        if motion_enabled():
            self.count_pulse.stop()
            self.count_pulse.start()

    def open_day(self, day):
        self.select(day)
        view = DayDetails(self, day, self.today, self.snapshot)
        view.add.connect(self.open_editor)
        view.details.connect(self.open_details)
        self._present(view)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and hasattr(self, "day_timer"):
            self.check_day()
            QTimer.singleShot(0, self.reload)

    def closeEvent(self, event):
        self._closing = True
        self.day_timer.stop()
        super().closeEvent(event)
