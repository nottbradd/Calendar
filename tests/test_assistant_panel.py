"""Use the real Qt drawer and a disposable calendar, with mocked Responses traffic."""

import json
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from school_calendar import assistant_api as api
from school_calendar.assistant_config import DEFAULTS
from school_calendar.model import Entry
from school_calendar.storage import Store
from school_calendar.window import CalendarWindow


class AssistantPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait_for(self, condition):
        until = monotonic() + 5
        while not condition() and monotonic() < until:
            QTest.qWait(15)
        self.assertTrue(condition())

    def test_failed_send_discard_stale_refresh_and_apply(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "calendar.sqlite3"
        store = self.enterContext(closing(Store(path)))
        today = date(2031, 4, 14)
        self.enterContext(patch("school_calendar.assistant_config.options", return_value=DEFAULTS))
        self.enterContext(patch("school_calendar.assistant_config.project_key", return_value="sample-key"))
        self.enterContext(
            patch.object(api, "post", side_effect=AssertionError("Unexpected network access"))
        )
        # The drawer must rediscover the visible calendar on each request.
        window = CalendarWindow(store, clock=lambda: today)
        self.addCleanup(window.close)
        window.resize(800, 540)
        window.show()
        window.assistant_button.click()
        panel = window.assistant_panel
        self.assertTrue(panel.isVisible())
        self.assertTrue(window.centralWidget().rect().contains(panel.geometry()))
        text = "Add a fictional library session today."
        changes = {
            **dict.fromkeys(api.CHANGES),
            "title": "Fictional library session",
            "day": today.isoformat(),
        }
        arguments = {"actions": [{"op": "create", "id": None, "changes": changes}], "fixed_ids": []}
        result = {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "propose_changes",
                    "call_id": "sample-proposal",
                    "arguments": json.dumps(arguments),
                }
            ],
        }
        responses = self.enterContext(
            patch.object(
                api,
                "response",
                side_effect=[
                    api.ApiError("Sample offline error."),
                    result,
                    result,
                    result,
                ],
            )
        )
        panel.composer.setText(text)
        panel.send_button.click()
        self.wait_for(lambda: panel.token is None)
        self.assertEqual(panel.composer.text(), text)
        self.assertIn("offline", panel.status.text())
        self.assertIsNone(panel.plan)

        panel.send_button.click()
        self.wait_for(lambda: panel.apply_button.isVisible())
        self.assertIn(changes["title"], panel.review.toPlainText())
        self.assertFalse(store.read().entries)
        panel.discard.click()
        self.assertFalse(panel.apply_button.isVisible())
        self.assertFalse(store.read().entries)

        panel.composer.setText(text)
        panel.send_button.click()
        self.wait_for(lambda: panel.apply_button.isVisible())
        with closing(Store(path)) as other:
            other.save(Entry("External fictional entry", today))
        revised_request = text + " Leave my other entry as it is."
        panel.composer.setText(revised_request)
        panel.apply_button.click()
        self.assertFalse(panel.apply_button.isVisible())
        self.assertIn("fresh proposal", panel.status.text())
        self.assertEqual(panel.composer.text(), revised_request)
        self.assertEqual([entry.title for entry in store.read().entries], ["External fictional entry"])

        panel.send_button.click()
        self.wait_for(lambda: panel.apply_button.isVisible())
        panel.apply_button.click()
        self.assertFalse(panel.apply_button.isVisible())
        self.assertEqual(
            {entry.title for entry in window.snapshot.entries},
            {"External fictional entry", changes["title"]},
        )
        self.assertIn("Changes saved", panel.chat.toPlainText())
        self.assertEqual(responses.call_count, 4)
        panel.clear()
        self.assertFalse(panel.history)
        self.assertEqual(panel.chat.toPlainText(), "")
