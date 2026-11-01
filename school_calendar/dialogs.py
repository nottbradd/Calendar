from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QLocale, QPoint, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget, QWidgetAction,
)

from .agenda import EntryList, status_badge, tone_for
from .appearance import animate_property, button, icon, label, paint_frost
from .model import Entry, KINDS, countdown, full_day, parse_day, parse_time
from .storage import Snapshot, Store, StoreError
from .windows import enable_system_frost


class PlannerDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.native_frost = enable_system_frost(self)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(8)

    def paintEvent(self, _event):
        paint_frost(self, self.native_frost)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.native_frost:
            animate_property(self, self, b"windowOpacity", .93, 1.0)

    def error_label(self):
        result = label("", "error")
        result.setAccessibleName("Error")
        result.setWordWrap(True)
        result.hide()
        return result


def field(caption, control, buddy=None):
    box = QVBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(3)
    name = label(caption, "field")
    target = buddy if buddy is not None else control
    name.setBuddy(target)
    target.setAccessibleName(caption)
    box.addWidget(name)
    box.addWidget(control)
    return box


class DateInput(QWidget):
    def __init__(self, day: date):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.text = QLineEdit()
        self.text.setObjectName("dateInput")
        self.text.setPlaceholderText("DD/MM/YYYY")
        self.set_day(day)
        layout.addWidget(self.text, 1)
        self.pick = button("", "", "calendar")
        self.pick.setAccessibleName("Choose date from calendar")
        self.pick.setToolTip("Choose date from calendar")
        self.pick.setFixedWidth(34)
        layout.addWidget(self.pick)
        self.menu = QMenu(self)
        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale("en_AU"))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setMinimumDate(QDate(1, 1, 1))
        self.calendar.setMaximumDate(QDate(9999, 12, 31))
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(self.calendar)
        self.menu.addAction(action)
        self.pick.clicked.connect(self.show_calendar)
        self.calendar.clicked.connect(self.picked)
        self.calendar.activated.connect(self.picked)

    def set_day(self, day):
        self.text.setText(f"{day.day:02}/{day.month:02}/{day.year:04}")

    def show_calendar(self):
        try:
            day = parse_day(self.text.text())
        except ValueError:
            day = date.today()
        self.calendar.setSelectedDate(QDate(day.year, day.month, day.day))
        self.menu.popup(self.mapToGlobal(QPoint(0, self.height())))
        self.calendar.setFocus()

    def picked(self, day):
        self.set_day(day.toPython())
        self.menu.close()
        self.text.setFocus()


class EntryEditor(PlannerDialog):
    saved = Signal(object)

    def __init__(self, parent, store: Store, entry: Entry, subjects):
        title = "Edit entry" if entry.revision else "Add entry"
        super().__init__(parent, title)
        self.store, self.original = store, entry
        self.setMinimumWidth(432)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        self.title_input = QLineEdit(entry.title)
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("What’s coming up?")
        grid.addLayout(field("Title *", self.title_input), 0, 0, 1, 2)
        self.subject_input = QComboBox()
        self.subject_input.setEditable(True)
        self.subject_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.subject_input.addItems(list(subjects))
        self.subject_input.setCurrentIndex(-1)
        self.subject_input.setEditText(entry.subject)
        self.subject_input.lineEdit().setPlaceholderText("Choose or type a subject")
        grid.addLayout(field("Subject", self.subject_input), 1, 0)
        self.kind_input = QComboBox()
        self.kind_input.addItems(KINDS)
        self.kind_input.setCurrentText(entry.kind)
        grid.addLayout(field("Type", self.kind_input), 1, 1)
        self.custom_input = QLineEdit(entry.custom_kind)
        self.custom_input.setPlaceholderText("e.g. Study session")
        self.custom_box = QWidget()
        self.custom_box.setLayout(field("Custom type name (optional)", self.custom_input))
        grid.addWidget(self.custom_box, 2, 0, 1, 2)
        self.custom_box.setVisible(entry.kind == "Custom")
        self.kind_input.currentTextChanged.connect(self._kind_changed)
        self.date_input = DateInput(entry.day)
        grid.addLayout(field("Date *", self.date_input, self.date_input.text), 3, 0)
        self.time_input = QLineEdit(entry.at.strftime("%H:%M") if entry.at else "")
        self.time_input.setPlaceholderText("All day")
        grid.addLayout(field("Time (24-hour)", self.time_input), 3, 1)
        self.notes_input = QPlainTextEdit(entry.notes)
        self.notes_input.setFixedHeight(66)
        self.notes_input.setTabChangesFocus(True)
        self.notes_input.setPlaceholderText("Details, chapters or things to bring")
        grid.addLayout(field("Notes (optional)", self.notes_input), 4, 0, 1, 2)
        self.error = self.error_label()
        self.body.addWidget(self.error)
        actions = QHBoxLayout()
        self.cancel_button = button("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = button("Save entry", "primary", "check")
        self.save_button.setToolTip("Save entry (Ctrl+Enter or Ctrl+S)")
        self.save_button.clicked.connect(self.save)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        self.body.addLayout(actions)
        for keys in ("Ctrl+Return", "Ctrl+Enter", "Ctrl+S"):
            QShortcut(QKeySequence(keys), self, activated=self.save)
        self.setTabOrder(self.title_input, self.subject_input)
        self.setTabOrder(self.subject_input, self.kind_input)
        self.setTabOrder(self.kind_input, self.custom_input)
        self.setTabOrder(self.custom_input, self.date_input.text)
        self.setTabOrder(self.date_input.text, self.date_input.pick)
        self.setTabOrder(self.date_input.pick, self.time_input)
        self.setTabOrder(self.time_input, self.notes_input)
        self.resize(466, self.sizeHint().height())

    def showEvent(self, event):
        super().showEvent(event)
        self.title_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self.title_input.selectAll()

    def _kind_changed(self, value):
        self.custom_box.setVisible(value == "Custom")
        self.adjustSize()

    def _invalid(self, control, message):
        control.setProperty("invalid", True)
        control.style().polish(control)
        control.setFocus()
        self.error.setText(message)
        self.error.show()

    def save(self):
        for control in (self.title_input, self.date_input.text, self.time_input):
            control.setProperty("invalid", False)
            control.style().polish(control)
        if not self.title_input.text().strip():
            self._invalid(self.title_input, "Give this entry a title.")
            return
        try:
            day = parse_day(self.date_input.text.text())
        except ValueError as exc:
            self._invalid(self.date_input.text, str(exc))
            return
        try:
            at = parse_time(self.time_input.text())
        except ValueError as exc:
            self._invalid(self.time_input, str(exc))
            return
        updated = replace(
            self.original, title=self.title_input.text(), day=day, subject=self.subject_input.currentText(),
            kind=self.kind_input.currentText(), custom_kind=self.custom_input.text(),
            at=at, notes=self.notes_input.toPlainText(),
        )
        try:
            self.store.save(updated)
        except StoreError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.saved.emit(updated.day)
        self.accept()


class EntryDetails(PlannerDialog):
    edit = Signal(object)
    changed = Signal()

    def __init__(self, parent, store: Store, entry: Entry, colour: str, today: date):
        super().__init__(parent, "Entry details")
        self.store, self.entry = store, entry
        self.setMinimumWidth(406)
        title = label(entry.title, "heading")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setMaximumHeight(94)
        title.setToolTip(entry.title)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(title)
        tags = QHBoxLayout()
        subject = label(entry.subject or "No subject")
        subject.setWordWrap(True)
        subject.setToolTip(entry.subject)
        subject.setStyleSheet(f"color:#28384e; border-left:3px solid {colour}; background:#eaf0f7; padding:3px 7px; font-size:12px;")
        tags.addWidget(subject)
        kind = label(entry.type_label, "muted")
        kind.setWordWrap(True)
        tags.addWidget(kind)
        tags.addStretch()
        self.body.addLayout(tags)
        self.body.addSpacing(3)
        for glyph, value in (("calendar", full_day(entry.day)), ("clock", entry.time_label)):
            line = QHBoxLayout()
            symbol = QLabel()
            symbol.setPixmap(icon(glyph).pixmap(17, 17))
            line.addWidget(symbol)
            line.addWidget(label(value))
            line.addStretch()
            if glyph == "calendar":
                line.addWidget(status_badge(countdown(entry, today), tone_for(entry, today)))
            self.body.addLayout(line)
        if entry.notes:
            self.body.addWidget(label("Notes", "field"))
            notes = QPlainTextEdit(entry.notes)
            notes.setObjectName("detailNotes")
            notes.setReadOnly(True)
            notes.setAccessibleName("Notes")
            notes.setTabChangesFocus(True)
            notes.setFixedHeight(88)
            self.body.addWidget(notes)
        self.error = self.error_label()
        self.body.addWidget(self.error)
        self.body.addSpacing(4)
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.delete_button = button("Delete", "danger")
        self.edit_button = button("Edit", "", "edit")
        self.complete_button = button("Reopen" if entry.completed else "Mark complete", "primary", "reopen" if entry.completed else "check")
        row.addWidget(self.delete_button)
        row.addStretch()
        row.addWidget(self.edit_button)
        row.addWidget(self.complete_button)
        self.body.addWidget(self.actions)
        self.confirmation = QWidget()
        confirm_row = QHBoxLayout(self.confirmation)
        confirm_row.setContentsMargins(0, 0, 0, 0)
        confirm_row.addWidget(label("Delete this entry?", "field"))
        confirm_row.addStretch()
        self.keep_button = button("Keep entry")
        self.confirm_delete = button("Delete entry", "danger")
        confirm_row.addWidget(self.keep_button)
        confirm_row.addWidget(self.confirm_delete)
        self.body.addWidget(self.confirmation)
        self.confirmation.hide()
        self.delete_button.clicked.connect(lambda: self._confirming(True))
        self.keep_button.clicked.connect(lambda: self._confirming(False))
        self.confirm_delete.clicked.connect(lambda: self._change(delete=True))
        self.complete_button.clicked.connect(lambda: self._change(delete=False))
        self.edit_button.clicked.connect(self._edit)
        self.edit_button.setFocus()
        self.resize(448, self.sizeHint().height())

    def _confirming(self, confirm):
        self.actions.setVisible(not confirm)
        self.confirmation.setVisible(confirm)
        (self.keep_button if confirm else self.delete_button).setFocus()

    def _edit(self):
        self.accept()
        self.edit.emit(self.entry)

    def _change(self, *, delete: bool):
        try:
            if delete:
                self.store.delete(self.entry)
            else:
                self.store.save(replace(self.entry, completed=not self.entry.completed))
        except StoreError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.changed.emit()
        self.accept()


class DayDetails(PlannerDialog):
    add = Signal(object)
    details = Signal(object)

    def __init__(self, parent, day: date, today: date, snapshot: Snapshot):
        super().__init__(parent, "Day entries")
        self.body.addWidget(label(full_day(day), "subheading"))
        entries = [entry for entry in snapshot.entries if entry.day == day]
        self.rows = EntryList()
        self.rows.display(entries, snapshot.colours, today, groups=False, empty_title="A clear day", empty_body="Add an entry for this date.")
        self.rows.open_entry.connect(self._open)
        self.body.addWidget(self.rows, 1)
        actions = QHBoxLayout()
        close = button("Close")
        close.clicked.connect(self.reject)
        self.add_button = button("Add entry", "primary", "plus")
        self.add_button.clicked.connect(lambda: self._add(day))
        actions.addStretch()
        actions.addWidget(close)
        actions.addWidget(self.add_button)
        self.body.addLayout(actions)
        self.resize(406, 359)

    def _open(self, entry):
        self.accept()
        self.details.emit(entry)

    def _add(self, day):
        self.accept()
        self.add.emit(day)
