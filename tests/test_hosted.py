"""Fictional devices, synthetic PCM and mocked transport. No Cloudflare or OpenAI traffic."""

from contextlib import closing
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
import unittest
import wave

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from school_calendar import assistant_api as api, assistant_config as config, assistant_hosted as hosted
from school_calendar.storage import Store
from school_calendar.window import CalendarWindow


class HostedTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"LOCALAPPDATA": folder, "OPENAI_API_KEY": ""}))
        self.path = Path(folder) / "calendar.sqlite3"
        self.invite = "CAI1." + "a" * 43
        self.seen = []
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)

    def enrollment(self, bearer, path, body, mime, cancel, **kwargs):
        self.assertEqual(kwargs["host"], hosted.HOST)
        self.assertEqual(path, "enroll")
        self.assertEqual(json.loads(body), {"invite": self.invite[5:]})
        self.seen.append(bearer)
        return {"device": bearer.split(".")[0], "member": "a" * 32}

    def test_consent_and_lost_enrollment_reply_preserve_personal_profile(self):
        config.save_key("fictional-private-project-key")
        config.save_options({**config.DEFAULTS, "mode": "GPT-5.4"})
        with patch.object(api, "post", side_effect=self.enrollment) as post:
            with self.assertRaises(ValueError):
                hosted.activate(self.invite, False, api.Cancellation())
            post.assert_not_called()

            def lost(*args, **kwargs):
                self.enrollment(*args, **kwargs)
                raise api.ApiError("Network interrupted")

            post.side_effect = lost
            with self.assertRaises(api.ApiError):
                hosted.activate(self.invite, True, api.Cancellation())
            self.assertFalse(hosted.active())
            post.side_effect = self.enrollment
            hosted.activate(self.invite, True, api.Cancellation())
        self.assertEqual(self.seen[0], self.seen[1])
        self.assertEqual(hosted.provider(), "Personal")
        with self.assertRaises(ValueError):
            hosted.select("Hosted", False)
        hosted.select("Hosted", True)
        self.assertEqual(hosted.credential(), self.seen[0])
        for path in config.directory().iterdir():
            content = path.read_bytes()
            for secret in (self.seen[0].split(".")[1], self.invite, "fictional-private-project-key"):
                self.assertNotIn(secret.encode(), content)
        hosted.forget()
        with self.assertRaises(api.ApiError):
            hosted.credential()
        hosted.select("Personal")
        self.assertEqual(config.project_key(), "fictional-private-project-key")
        self.assertEqual(config.options()["mode"], "GPT-5.4")

    def test_hosted_failure_keeps_draft_and_retries_same_operation_without_personal_fallback(self):
        with patch.object(api, "post", side_effect=self.enrollment):
            hosted.activate(self.invite, True, api.Cancellation())
        hosted.select("Hosted", True)
        store = self.enterContext(closing(Store(self.path)))
        window = CalendarWindow(store)
        self.addCleanup(window.close)
        window.open_assistant()
        panel = window.assistant_panel
        requests = []

        def fail(bearer, path, body, mime, cancel, **kwargs):
            self.assertEqual(kwargs["host"], hosted.HOST)
            self.assertEqual(bearer, self.seen[0])
            self.assertEqual(path, "chat")
            payload = json.loads(body)
            self.assertEqual(set(payload), {"turn", "step", "input", "date", "zone"})
            requests.append(payload["turn"])
            raise api.ApiError(hosted.error_message(429, "shared_budget"))

        with (
            patch.object(api, "post", side_effect=fail),
            patch.object(config, "project_key", side_effect=AssertionError("Personal key read")),
        ):
            panel.composer.setText("Keep my fictional draft.")
            for _ in range(2):
                panel.send_button.click()
                end = monotonic() + 4
                while panel.token and monotonic() < end:
                    QTest.qWait(10)
                self.assertFalse(panel.token)
                self.assertEqual(panel.composer.text(), "Keep my fictional draft.")
                self.assertIn("shared spending", panel.status.text())
                self.assertFalse(store.read().entries)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], requests[1])
        self.assertFalse(panel.plan)

    def test_audio_sends_only_known_pcm_format_and_hosted_device_bearer(self):
        audio = io.BytesIO()
        with wave.open(audio, "wb") as f:
            f.setparams((1, 2, 16000, 100, "NONE", ""))
            f.writeframes(b"\x01\x00" * 100)
        with patch.object(api, "post", return_value={"text": "Fictional transcript"}) as post:
            self.assertEqual(
                hosted.transcribe("fictional-device", audio.getvalue(), api.Cancellation()),
                "Fictional transcript",
            )
            self.assertEqual(
                post.call_args.args[:4],
                ("fictional-device", "transcribe", b"\x01\x00" * 100, "application/octet-stream"),
            )
            self.assertEqual(post.call_args.kwargs["host"], hosted.HOST)
            self.assertRegex(
                post.call_args.kwargs["extra_headers"]["X-Calendar-Request"], r"^\d+-[0-9a-f]{32}$"
            )
