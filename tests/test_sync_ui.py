"""Drive the real Settings dialogs. Set CALENDER_LOCAL_UI_TRIAL=1 for the local Wrangler trial."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QInputDialog, QLineEdit, QPushButton

from school_calendar.appearance import apply_theme
from school_calendar.storage import Store
from school_calendar.sync_client import Api, summary
from school_calendar.sync_ui import SyncDialog
from school_calendar.window import CalendarWindow


class SyncInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def device(self, name):
        store = Store(Path(self.temp.name) / name / "calendar.sqlite3")
        self.addCleanup(store.close)
        window = CalendarWindow(store)
        self.addCleanup(window.close)
        window.sync_control.api_factory = lambda: Api("http://127.0.0.1:18787")
        window.show()
        window.open_settings()
        window.dialog.sync_button.click()
        self.assertIsInstance(window.dialog, SyncDialog)
        return window, window.dialog

    def wait(self, condition):
        until = monotonic() + 15
        while not condition() and monotonic() < until:
            self.app.processEvents()
            QTest.qWait(15)
        self.assertTrue(condition())

    def test_disabled_settings_and_cancelled_creation_leave_calendar_offline(self):
        window, settings = self.device("Cancel")
        self.assertEqual(settings.status_label.text(), "Sync disabled")
        QTimer.singleShot(100, lambda: self.app.activeModalWidget().reject())
        settings.setup_row[0].click()
        self.assertEqual(summary(window.store.db)["phase"], "disabled")

    @unittest.skipUnless(os.environ.get("CALENDER_LOCAL_UI_TRIAL") == "1", "requires local Wrangler on port 18787")
    def test_create_join_compare_and_approve_through_settings(self):
        a, left = self.device("A")
        b, right = self.device("B")

        def accept_recovery():
            dialog = self.app.activeModalWidget()
            self.assertTrue(dialog.findChild(QLineEdit).text().startswith("CALR1."))
            dialog.findChild(QCheckBox).setChecked(True)
            dialog.findChild(QPushButton).click()

        def enter(text):
            def fill():
                dialog = self.app.activeModalWidget()
                self.assertIsInstance(dialog, QInputDialog)
                dialog.setTextValue(text)
                dialog.accept()
            return fill

        QTimer.singleShot(80, accept_recovery)
        left.setup_row[0].click()
        self.wait(lambda: not a.sync_control.busy)
        self.assertEqual(summary(a.store.db)["phase"], "active", a.sync_control.error)
        left.manage[0].click()
        self.wait(lambda: not a.sync_control.busy)
        self.assertTrue(left.code.text().startswith("CALI1."))
        QTimer.singleShot(80, enter(left.code.text()))
        right.setup_row[1].click()
        self.wait(lambda: not b.sync_control.busy)
        self.assertIn("Waiting for approval", right.status_label.text())
        self.assertEqual(len(right.code.text()), 12)
        left.footer[2].click()
        self.wait(lambda: not a.sync_control.busy)
        self.assertTrue(left.manage[1].isEnabled())
        QTimer.singleShot(80, enter(right.code.text()))
        left.manage[1].click()
        self.wait(lambda: not a.sync_control.busy)
        right.footer[2].click()
        self.wait(lambda: not b.sync_control.busy)
        self.assertEqual(summary(b.store.db)["phase"], "active", b.sync_control.error)
        self.assertEqual(right.devices.count(), 2)
        self.assertFalse(right.code.isVisible())
