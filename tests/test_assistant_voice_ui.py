"""Drive Talk, Stop and Cancel in a real Qt drawer with synthetic audio and no network."""

import io
import os
import threading
import unittest
import wave
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from school_calendar import assistant_ui
from school_calendar.storage import Store
from school_calendar.window import CalendarWindow


class SampleRecorder:
    def __init__(self):
        self.started = threading.Event()
        self.done = threading.Event()

    def capture(self, stop, cancel):
        self.started.set()
        try:
            deadline = monotonic() + 4
            while not stop.wait(0.01):
                if cancel.is_set():
                    raise InterruptedError()
                if monotonic() >= deadline:
                    raise AssertionError("Recording did not receive Stop or Cancel")
            stream = io.BytesIO()
            with wave.open(stream, "wb") as wav:
                wav.setparams((1, 2, 16000, 1600, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x01\x00\xff" * 800)
            return stream.getvalue()
        finally:
            self.done.set()


class AssistantVoiceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.enterContext(
            patch.dict(os.environ, {"LOCALAPPDATA": temp, "OPENAI_API_KEY": "fictional-ui-key"})
        )
        self.store = self.enterContext(closing(Store(Path(temp) / "sample.sqlite3")))
        self.window = CalendarWindow(self.store)
        self.addCleanup(self.window.close)
        self.window.show()
        self.window.assistant_button.click()
        self.panel = self.window.assistant_panel
        self.recorder = SampleRecorder()
        self.enterContext(patch.object(assistant_ui, "Recorder", return_value=self.recorder))
        self.network = self.enterContext(
            patch.object(
                assistant_ui.api.http.client,
                "HTTPSConnection",
                side_effect=AssertionError("Network disabled"),
            )
        )

    def wait(self, predicate):
        deadline = monotonic() + 4
        while not predicate() and monotonic() < deadline:
            QTest.qWait(15)
        self.assertTrue(predicate(), self.panel.status.text())

    def test_hold_release_inserts_editable_transcript_before_send(self):
        panel = self.panel
        panel.composer.setText("Please")
        with patch.object(
            assistant_ui.api, "transcribe", return_value="find my fictional report"
        ) as transcribe:
            QTest.mousePress(panel.mic_button, Qt.MouseButton.LeftButton)
            self.wait(self.recorder.started.is_set)
            self.assertTrue(panel.stop_button.isVisible())
            self.assertTrue(panel.cancel_button.isVisible())
            QTest.qWait(440)
            QTest.mouseRelease(panel.mic_button, Qt.MouseButton.LeftButton)
            self.wait(lambda: panel.token is None)
        transcribe.assert_called_once()
        self.assertEqual(panel.composer.text(), "Please find my fictional report")
        self.assertFalse(panel.composer.isReadOnly())
        self.assertTrue(panel.send_button.isEnabled())
        self.assertEqual(panel.chat.toPlainText(), "")
        self.network.assert_not_called()

    def test_cancel_recording_preserves_draft_and_does_not_transcribe(self):
        panel = self.panel
        panel.composer.setText("My original draft")
        panel.mic_button.click()
        self.wait(self.recorder.started.is_set)
        panel.cancel_button.click()
        self.wait(self.recorder.done.is_set)
        self.assertFalse(panel.stop_button.isVisible())
        self.assertFalse(panel.cancel_button.isVisible())
        self.assertEqual(panel.composer.text(), "My original draft")
        self.assertEqual(panel.chat.toPlainText(), "")
        self.network.assert_not_called()

    def test_failed_transcription_preserves_draft_and_allows_retry(self):
        panel = self.panel
        panel.composer.setText("Keep this draft")
        self.network.side_effect = None
        http = self.network.return_value
        reply = http.getresponse.return_value
        reply.status = 429
        reply.read.return_value = (
            b'{"error":{"type":"insufficient_quota","code":"credit_balance_exhausted"}}'
        )
        panel.mic_button.click()
        self.wait(self.recorder.started.is_set)
        panel.stop_button.click()
        self.wait(lambda: panel.token is None)
        http.request.assert_called_once()
        self.assertEqual(http.request.call_args.args[:2], ("POST", "/v1/audio/transcriptions"))
        self.assertIn("add credits", panel.status.text().lower())
        self.assertEqual(panel.composer.text(), "Keep this draft")
        self.assertTrue(panel.mic_button.isEnabled())
        self.assertTrue(panel.send_button.isEnabled())
        self.assertFalse(self.store.read().entries)
