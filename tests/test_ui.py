"""Drive actual Qt windows and controls against a disposable SQLite calendar."""
import unittest
from datetime import date, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from school_calendar.agenda import EntryRow
from school_calendar.appearance import apply_theme
from school_calendar.dialogs import DayDetails, EntryDetails, EntryEditor
from school_calendar.model import Entry, SUBJECT_COLOURS, countdown, parse_day
from school_calendar.month_view import chip_layout
from school_calendar.storage import Store
from school_calendar.window import CalendarWindow


class CalendarInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "calendar.sqlite3"
        self.today = date(2026, 11, 4)
        self.open_app()

    def open_app(self):
        self.store = Store(self.path)
        self.win = CalendarWindow(self.store, lambda: self.today)
        self.win.resize(1120, 714)
        self.win.show()
        self.win.activateWindow()
        self.assertTrue(QTest.qWaitForWindowExposed(self.win, 3000))
        self.settle()

    def close_app(self):
        if self.win.dialog is not None:
            self.win.dialog.reject()
        self.win.close()
        self.win.deleteLater()
        self.settle()
        self.store.close()

    def tearDown(self):
        self.close_app()
        self.directory.cleanup()

    def settle(self):
        QTest.qWait(35)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def click(self, widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.settle()

    def key(self, widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(widget, key, modifiers)
        self.settle()

    def type_text(self, widget, value):
        widget.setFocus()
        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        if value:
            QTest.keyClicks(widget, value)
        else:
            QTest.keyClick(widget, Qt.Key.Key_Backspace)

    def open_row(self, title):
        row, = [r for r in self.win.agenda.widget().findChildren(EntryRow) if r.entry.title == title]
        self.win.agenda.ensureWidgetVisible(row)
        self.click(row)
        self.assertIsInstance(self.win.dialog, EntryDetails)
        return self.win.dialog

    def click_date(self, day):
        index = self.win.grid.month_model.index_of(day)
        self.assertTrue(index.isValid())
        point = self.win.grid.visualRect(index).topLeft() + QPoint(10, 9)
        QTest.mouseClick(self.win.grid.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.settle()

    def test_create_edit_complete_reopen_delete_and_restart(self):
        self.click(self.win.add_button)
        editor = self.win.dialog
        self.assertIsInstance(editor, EntryEditor)
        self.assertTrue(editor.title_input.hasFocus())
        self.click(editor.save_button)
        self.assertIn("title", editor.error.text())
        self.type_text(editor.title_input, "Portfolio")
        self.key(editor.title_input, Qt.Key.Key_Tab)
        self.assertTrue(editor.subject_input.hasFocus() or editor.subject_input.lineEdit().hasFocus())
        self.type_text(editor.subject_input.lineEdit(), "Art")
        self.type_text(editor.date_input.text, "")
        self.click(editor.save_button)
        self.assertIn("date", editor.error.text())
        self.type_text(editor.date_input.text, "02/11/2026")
        self.type_text(editor.notes_input, "Bring sketchbook")
        self.key(editor.notes_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertIsNone(self.win.dialog)
        portfolio, = self.win.snapshot.entries
        self.assertEqual((portfolio.day, portfolio.at), (date(2026, 11, 2), None))
        self.assertEqual(countdown(portfolio, self.today), "2 days overdue")
        self.assertEqual(self.win.grid.month_model.by_day[portfolio.day], [portfolio])

        tomorrow = self.today + timedelta(days=1)
        self.click_date(tomorrow)
        editor = self.win.dialog
        self.assertEqual(parse_day(editor.date_input.text.text()), tomorrow)
        self.type_text(editor.title_input, "Oral presentation")
        self.type_text(editor.subject_input.lineEdit(), "English")
        self.type_text(editor.time_input, "10:30")
        self.click(editor.save_button)
        entries = self.win.snapshot.entries
        self.assertEqual([e.title for e in entries], ["Portfolio", "Oral presentation"])
        self.assertEqual(entries[1].at, time(10, 30))
        self.assertEqual([r.entry for r in self.win.agenda.widget().findChildren(EntryRow)], list(entries))
        labels = [w.text() for w in self.win.agenda.widget().findChildren(QLabel)]
        self.assertIn("2 days overdue", labels)
        self.assertIn("Tomorrow", labels)

        details = self.open_row("Portfolio")
        self.click(details.edit_button)
        editor = self.win.dialog
        self.assertEqual(editor.title_input.text(), "Portfolio")
        self.type_text(editor.subject_input.lineEdit(), "Design")
        self.type_text(editor.date_input.text, "09/11/2026")
        self.type_text(editor.notes_input, "Mount the final pages")
        self.key(editor.notes_input, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual([e.title for e in self.win.snapshot.entries], ["Oral presentation", "Portfolio"])
        self.assertFalse(self.win.grid.month_model.by_day.get(portfolio.day))
        self.assertEqual(self.win.snapshot.entries[1].id, portfolio.id)
        expected_colours = self.win.snapshot.colours.copy()
        self.assertEqual(len(set(expected_colours.values())), 3)

        self.click(self.open_row("Portfolio").complete_button)
        self.assertEqual([r.entry.title for r in self.win.agenda.widget().findChildren(EntryRow)], ["Oral presentation"])
        details = self.open_row("Oral presentation")
        self.click(details.delete_button)
        self.click(details.keep_button)
        self.assertTrue(details.actions.isVisible())
        self.click(details.delete_button)
        self.click(details.confirm_delete)
        self.assertIsNone(self.win.dialog)
        self.assertEqual(len(self.win.snapshot.entries), 1)
        self.close_app()
        self.open_app()
        item, = self.win.snapshot.entries
        self.assertEqual((item.id, item.subject, item.notes, item.day, item.completed),
                         (portfolio.id, "Design", "Mount the final pages", date(2026, 11, 9), True))
        self.assertEqual(expected_colours, self.win.snapshot.colours)
        self.click(self.win.completed_button)
        self.click(self.open_row("Portfolio").complete_button)
        self.click(self.win.upcoming_button)
        self.assertFalse(self.open_row("Portfolio").entry.completed)
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.close_app()
        self.open_app()
        self.assertFalse(self.win.snapshot.entries[0].completed)

    def test_navigation_empty_states_crowding_and_compact_keyboard_use(self):
        self.assertIn("Nothing scheduled", [w.text() for w in self.win.agenda.findChildren(QLabel)])
        self.click(self.win.completed_button)
        self.assertIn("No completed work", [w.text() for w in self.win.agenda.findChildren(QLabel)])
        self.click(self.win.upcoming_button)
        self.click(self.win.following)
        self.assertEqual(self.win.month_label.text(), "December 2026")
        self.click(self.win.previous)
        self.click(self.win.previous)
        self.assertEqual(self.win.month_label.text(), "October 2026")
        self.click(self.win.today_button)
        self.assertEqual(self.win.selected, self.today)
        self.win.grid.setFocus()
        self.key(self.win.grid, Qt.Key.Key_Right)
        day = self.today + timedelta(days=1)
        self.assertEqual(self.win.selected, day)
        self.assertEqual(self.win.grid.today, self.today)
        self.key(self.win.grid, Qt.Key.Key_Space)
        self.assertEqual(parse_day(self.win.dialog.date_input.text.text()), day)
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.assertIsNone(self.win.dialog)
        self.assertFalse(self.win.snapshot.entries)
        for i in range(8):
            self.store.save(Entry(f"Workshop {i}", day, "History", at=time(8+i)))
        self.win.reload()
        self.win.resize(800, 540)
        self.settle()
        self.assertEqual(self.win.width(), 800)
        self.assertTrue(self.win.add_button.isVisible())
        self.assertTrue(self.win.completed_button.isVisible())
        index = self.win.grid.month_model.index_of(day)
        rect = self.win.grid.visualRect(index)
        boxes, more = chip_layout(rect, self.win.grid.month_model.by_day[day])
        self.assertLessEqual(len(boxes), 2)
        self.assertIsNotNone(more)
        self.assertTrue(rect.contains(more))
        QTest.mouseClick(self.win.grid.viewport(), Qt.MouseButton.LeftButton, pos=more.center())
        self.settle()
        self.assertIsInstance(self.win.dialog, DayDetails)
        rows = self.win.dialog.rows.widget().findChildren(EntryRow)
        self.assertEqual(len(rows), 8)
        self.win.dialog.rows.ensureWidgetVisible(rows[-1])
        self.click(rows[-1])
        self.assertEqual(self.win.dialog.entry.title, "Workshop 7")
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.win.grid.setFocus()
        self.key(self.win.grid, Qt.Key.Key_Return)
        self.assertIsInstance(self.win.dialog, DayDetails)
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.today = day
        self.win.check_day()
        self.assertIn("Today", [w.text() for w in self.win.agenda.findChildren(QLabel)])

    def test_failed_save_preserves_draft_and_can_be_retried(self):
        self.key(self.win, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        editor = self.win.dialog
        self.type_text(editor.title_input, "Keep my draft")
        self.type_text(editor.notes_input, "Unsaved notes")
        self.type_text(editor.time_input, "25:30")
        self.click(editor.save_button)
        self.assertIn("24-hour", editor.error.text())
        self.type_text(editor.time_input, "00:00")
        self.store.db.execute("PRAGMA query_only=ON")
        self.click(editor.save_button)
        self.assertIs(self.win.dialog, editor)
        self.assertIn("Could not save", editor.error.text())
        self.assertEqual(editor.title_input.text(), "Keep my draft")
        self.assertEqual(editor.notes_input.toPlainText(), "Unsaved notes")
        self.assertFalse(self.store.read().entries)
        self.store.db.execute("PRAGMA query_only=OFF")
        self.click(editor.save_button)
        self.assertEqual(self.win.snapshot.entries[0].at, time(0))

    def test_date_picker_custom_type_and_opening_a_chip(self):
        self.click_date(date(2026, 11, 30))
        editor = self.win.dialog
        self.type_text(editor.title_input, "Study group")
        self.key(editor.kind_input, Qt.Key.Key_End)
        self.assertTrue(editor.custom_box.isVisible())
        self.type_text(editor.custom_input, "Revision session")
        self.click(editor.date_input.pick)
        self.assertTrue(editor.date_input.menu.isVisible())
        self.key(editor.date_input.calendar.focusWidget(), Qt.Key.Key_Right)
        self.key(editor.date_input.calendar.focusWidget(), Qt.Key.Key_Return)
        self.assertEqual(parse_day(editor.date_input.text.text()), date(2026, 12, 1))
        self.click(editor.save_button)
        self.assertEqual(self.win.month_label.text(), "December 2026")
        item, = self.win.snapshot.entries
        index = self.win.grid.month_model.index_of(item.day)
        boxes, _ = chip_layout(self.win.grid.visualRect(index), [item])
        QTest.mouseClick(self.win.grid.viewport(), Qt.MouseButton.LeftButton, pos=boxes[0][0].center())
        self.settle()
        self.assertIsInstance(self.win.dialog, EntryDetails)
        self.assertEqual(self.win.dialog.entry.type_label, "Revision session")

    def test_subjects_and_ranges_in_create_and_edit_forms_survive_restart(self):
        self.store.save(Entry("Previously saved", self.today, "History", at=time(9)))
        self.win.reload()
        self.click(self.win.settings_button)
        settings = self.win.dialog
        self.assertEqual(settings.subjects.item(0).text(), "History")
        for name, message in ((" ", "Enter a subject"), (" HISTORY ", "already")):
            self.type_text(settings.name_input, name)
            self.click(settings.add_button)
            self.assertIn(message, settings.error.text())
            self.assertEqual(settings.name_input.text(), name)
        self.type_text(settings.name_input, "  Visual Arts ")
        self.click(settings.swatches[4])
        self.key(settings.name_input, Qt.Key.Key_Return)
        self.assertFalse(settings.error.isVisible())
        self.assertEqual(settings.subjects.count(), 2)
        self.assertEqual(self.win.snapshot.colours["Visual Arts"], SUBJECT_COLOURS[4])
        self.click(settings.done_button)
        self.click(self.win.add_button)
        editor = self.win.dialog
        self.assertEqual(editor.subject_input.findText("Visual Arts"), 1)
        editor.subject_input.showPopup()
        choices = editor.subject_input.view()
        self.assertTrue(choices.isVisible())
        choice = choices.model().index(1, 0)
        QTest.mouseClick(choices.viewport(), Qt.MouseButton.LeftButton, pos=choices.visualRect(choice).center())
        self.settle()
        self.assertEqual(editor.subject_input.currentText(), "Visual Arts")
        self.type_text(editor.title_input, "Colour study")
        self.type_text(editor.notes_input, "Bring folio")
        self.type_text(editor.end_input, "11:45")
        self.click(editor.save_button)
        self.assertIn("start time", editor.error.text())
        self.assertEqual(editor.end_input.text(), "11:45")
        self.type_text(editor.time_input, "10:15")
        for value, message in (("10:15", "later"), ("09:00", "same date"), ("25:00", "24-hour")):
            self.type_text(editor.end_input, value)
            self.click(editor.save_button)
            self.assertIn(message, editor.error.text())
            self.assertEqual((editor.time_input.text(), editor.end_input.text(), editor.notes_input.toPlainText()),
                             ("10:15", value, "Bring folio"))
        self.type_text(editor.end_input, "11:45")
        self.click(editor.save_button)
        self.assertIn("10:15–11:45", [w.text() for w in self.open_row("Colour study").findChildren(QLabel)])
        self.click(self.win.dialog.edit_button)
        self.assertEqual(self.win.dialog.subject_input.currentText(), "Visual Arts")
        self.assertEqual(self.win.dialog.end_input.text(), "11:45")
        self.click(self.win.dialog.cancel_button)
        old = self.open_row("Previously saved")
        self.assertIn("09:00 · No end time", [w.text() for w in old.findChildren(QLabel)])
        self.click(old.edit_button)
        self.assertEqual(self.win.dialog.subject_input.findText("Visual Arts"), 1)
        self.assertEqual(self.win.dialog.end_input.text(), "")
        self.click(self.win.dialog.cancel_button)
        self.click(self.win.week_button)
        expected = self.store.read()
        self.close_app()
        self.open_app()
        self.assertEqual(self.win.snapshot, expected)
        self.assertTrue(self.win.week.isVisible())
        self.assertTrue(self.win.week_button.isChecked())
        self.assertEqual(self.win.week.headings[self.today.weekday()].property("today"), True)

    def test_week_minutes_overlaps_short_and_out_of_hours_are_selectable(self):
        w = self.win.week
        day = self.today
        data = (("Lab", time(10,15), time(11,45)), ("Overlap", time(10,30), time(11,15)),
                ("Start only", time(10,30), None), ("Short", time(11,45), time(11,46)),
                ("Midnight", time(0), time(1)), ("Last minute", time(23,59), None))
        for title, start, end in data:
            self.store.save(Entry(title, day, "Science", at=start, end_at=end))
        for i in range(4):
            self.store.save(Entry(f"All day {i}", day))
        self.win.reload()
        self.click(self.win.week_button)
        widgets = {b.entry.title: b for b in w.timed[day.weekday()]}
        lab = widgets["Lab"]
        self.assertLessEqual(abs(lab.y() - w.y(600) - w.hour_height / 4), 1)
        self.assertLessEqual(abs(lab.duration_height - 1.5 * w.hour_height), 1)
        self.assertEqual(lab.height(), lab.duration_height)
        self.assertLessEqual(abs(w.scroll.verticalScrollBar().value() - 9 * w.hour_height), 1)
        self.assertEqual(widgets["Start only"].duration_height, 0)
        self.assertLessEqual(widgets["Short"].duration_height, 1)
        self.assertGreaterEqual(widgets["Short"].height(), 22)
        for other, another in (("Lab","Overlap"), ("Overlap","Start only"), ("Lab","Start only")):
            self.assertFalse(widgets[other].geometry().intersects(widgets[another].geometry()))
        for title, widget in widgets.items():
            w.scroll.ensureWidgetVisible(widget, 5, 5)
            self.settle()
            self.assertIs(w.canvas.childAt(widget.geometry().center()), widget)
            self.assertTrue(w.scroll.viewport().rect().contains(widget.mapTo(w.scroll.viewport(), widget.rect().center())))
            self.click(widget)
            self.assertEqual(self.win.dialog.entry.title, title)
            self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.assertEqual(w.all_day[day.weekday()][0].parentWidget(), w.header)
        self.click(w.more[day.weekday()])
        self.assertEqual(len(self.win.dialog.rows.widget().findChildren(EntryRow)), len(data) + 4)
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.click(w.all_day[day.weekday()][0])
        self.assertIsNone(self.win.dialog.entry.at)
        self.key(self.win.dialog, Qt.Key.Key_Escape)

    def test_week_navigation_slot_creation_and_updates_in_both_views(self):
        day = self.today + timedelta(days=1)
        self.win.select(day)
        self.click(self.win.week_button)
        w = self.win.week
        self.assertEqual((w.days[0].weekday(), w.days[-1].weekday(), self.win.selected), (0, 6, day))
        self.click(self.win.following)
        self.assertEqual(self.win.selected, day + timedelta(days=7))
        self.click(self.win.previous)
        self.assertEqual(self.win.selected, day)
        self.click(self.win.month_button)
        self.assertEqual(self.win.selected, day)
        self.click(self.win.following)
        self.assertEqual(self.win.selected.month, 12)
        self.click(self.win.week_button)
        self.click(self.win.today_button)
        self.assertEqual(self.win.selected, self.today)
        i = day.weekday()
        self.click(w.headings[i])
        self.assertEqual(parse_day(self.win.dialog.date_input.text.text()), day)
        self.assertEqual(self.win.dialog.time_input.text(), "")
        self.click(self.win.dialog.cancel_button)
        x = (w.edges[i] + w.edges[i + 1]) // 2
        QTest.mouseClick(w.canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, w.y(14 * 60 + 45)))
        self.settle()
        editor = self.win.dialog
        self.assertEqual((parse_day(editor.date_input.text.text()), editor.time_input.text(), editor.end_input.text()),
                         (day, "14:45", ""))
        self.type_text(editor.title_input, "Slot entry")
        self.type_text(editor.end_input, "16:00")
        self.click(editor.save_button)
        self.click(w.timed[i][0])
        self.click(self.win.dialog.edit_button)
        self.type_text(self.win.dialog.title_input, "Changed")
        self.type_text(self.win.dialog.end_input, "16:30")
        self.click(self.win.dialog.save_button)
        item = w.timed[i][0].entry
        self.assertEqual((item.title, item.end_at), ("Changed", time(16,30)))
        self.assertEqual(self.win.grid.month_model.by_day[day], [item])
        self.click(w.timed[i][0])
        self.click(self.win.dialog.complete_button)
        self.assertTrue(w.timed[i][0].entry.completed)
        self.assertTrue(self.win.grid.month_model.by_day[day][0].completed)
        self.win.resize(800, 540)
        self.settle()
        self.assertEqual(self.win.width(), 800)
        self.assertFalse(self.win.agenda_panel.isVisible())
        self.assertEqual(w.scroll.horizontalScrollBar().maximum(), 0)
        self.click(self.win.agenda_toggle)
        self.assertTrue(self.win.agenda_panel.isVisible())
        self.click(self.win.completed_button)
        self.click(self.open_row("Changed").complete_button)
        self.assertFalse(w.timed[i][0].entry.completed)
        self.click(w.timed[i][0])
        self.click(self.win.dialog.delete_button)
        self.click(self.win.dialog.confirm_delete)
        self.assertEqual(w.timed[i], [])
        self.click(self.win.month_button)
        self.assertFalse(self.win.grid.month_model.by_day.get(day))

    def test_busy_week_scrolls_with_its_dates_at_minimum_size(self):
        sunday = date(2027, 1, 3)
        for i in range(8):
            self.store.save(Entry(f"Session {i}", sunday, at=time(14), end_at=time(15)))
        self.win.reload()
        self.win.resize(760, 516)
        self.win.select(date(2026, 12, 31))
        self.click(self.win.week_button)
        w = self.win.week
        self.assertEqual((w.days[0], w.days[6]), (date(2026, 12, 28), sunday))
        self.assertEqual(self.win.size().toTuple(), (760, 516))
        bar = w.scroll.horizontalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        axis_position = w.axis.mapToGlobal(QPoint(0, 0))
        bar.setValue(bar.maximum())
        self.assertEqual(w.header.x(), -bar.value())
        self.assertEqual(w.axis.mapToGlobal(QPoint(0, 0)), axis_position)
        last = w.timed[6][-1]
        w.scroll.ensureWidgetVisible(last, 2, 2)
        self.settle()
        self.assertTrue(w.scroll.viewport().rect().contains(last.mapTo(w.scroll.viewport(), last.rect().center())))
        self.assertIs(w.canvas.childAt(last.geometry().center()), last)
        last.setFocus()
        self.key(last, Qt.Key.Key_Return)
        self.assertEqual(self.win.dialog.entry.title, "Session 7")
        self.key(self.win.dialog, Qt.Key.Key_Escape)
        self.win.select(date.min)
        self.assertFalse(self.win.previous.isEnabled())
        self.win.select(date.max)
        self.assertFalse(self.win.following.isEnabled())
        self.assertFalse(w.headings[-1].isEnabled())
