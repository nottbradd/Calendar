"""Exercise an isolated sponsored desktop profile. Local mode requires the mock load harness."""

import argparse
from contextlib import closing
from datetime import date
from getpass import getpass
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
from urllib.parse import urlsplit
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from school_calendar import assistant_api as api, assistant_config as config, assistant_hosted as hosted
from school_calendar.assistant_calendar import CalendarTools
from school_calendar.model import Entry
from school_calendar.storage import Store


def local_only(origin):
    address = urlsplit(origin)
    if address.scheme != "http" or address.hostname != "127.0.0.1" or not address.port:
        raise ValueError("Only the loopback mock server can be used here.")

    def transport(key, path, body, mime, cancel, *, host, error_hint, extra_headers=None):
        assert host == hosted.HOST
        cancel.check()
        conn = http.client.HTTPConnection("127.0.0.1", address.port, timeout=15)
        try:
            conn.request(
                "POST",
                "/v1/" + path,
                body,
                {"Content-Type": mime, "Authorization": "Bearer " + key, **(extra_headers or {})},
            )
            res = conn.getresponse()
            data = json.loads(res.read())
            if res.status != 200:
                raise api.ApiError(error_hint(res.status, data.get("error")))
            return data
        finally:
            conn.close()

    return transport


def local_desktop(store, invite):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from school_calendar.assistant_ui import AssistantSettings
    from school_calendar.window import CalendarWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = CalendarWindow(store)

    def wait(condition):
        end = monotonic() + 12
        while not condition() and monotonic() < end:
            QTest.qWait(15)
        assert condition()

    try:
        config.save_options({**config.DEFAULTS, "mode": "GPT-5.4"})
        config.save_key("fictional-local-private-key")
        window.show()
        dialog = AssistantSettings(window)
        dialog.show()
        dialog.provider.setCurrentIndex(1)
        dialog.consent.setChecked(True)
        dialog.invite.setText(invite)
        dialog.enroll.click()
        wait(lambda: dialog.activation is None)
        assert hosted.active(), dialog.device_status.text()
        dialog.save_button.click()
        assert hosted.provider() == "Hosted"
        window.open_assistant()
        panel = window.assistant_panel
        panel.composer.setText("Add a fictional hosted assessment.")
        panel.send_button.click()
        wait(lambda: panel.token is None)
        assert panel.plan is not None, panel.status.text()
        assert not store.read().entries
        reviewed = panel.plan
        with patch.object(api, "post", side_effect=api.ApiError("Service unavailable.")) as offline:
            panel.send()
            panel.record()
            assert panel.plan is reviewed and not store.read().entries
            panel.apply_button.click()
            offline.assert_not_called()
        assert store.read().entries[0].title == "Fictional hosted assessment"
        panel.composer.setText("Find my fictional assessment.")
        panel.send_button.click()
        wait(lambda: panel.token is None)
        assert "fictional lookup completed" in panel.chat.toPlainText()
        assert "Hosted" in panel.status.text()
        # Exact same audio format as the Windows recorder. No physical microphone.
        out = io.BytesIO()
        with wave.open(out, "wb") as f:
            f.setparams((1, 2, 16000, 32000, "NONE", ""))
            f.writeframes(b"\1\0" * 32000)

        class SampleRecorder:
            def capture(self, stop, cancel):
                assert stop.wait(4)
                return out.getvalue()

        with patch("school_calendar.assistant_ui.Recorder", SampleRecorder):
            panel.mic_button.click()
            panel.stop_button.click()
            wait(lambda: panel.token is None)
        assert panel.composer.text() == "Find my fictional exam."
        assert len(store.read().entries) == 1
        hosted.select("Personal")
        assert (
            config.project_key() == "fictional-local-private-key" and config.options()["mode"] == "GPT-5.4"
        )
        print(
            "Desktop activated a second device, used hosted lookup, reviewed and applied locally, edited a mock transcript and restored personal preferences."
        )
    finally:
        window.close()
        app.processEvents()


def live(store, invite, folder):
    # Use only this disposable profile, never a personal calendar or personal API key.
    member = hosted.activate(invite, True, api.Cancellation())
    hosted.select("Hosted", True)
    key = hosted.credential()
    print("Disposable device to revoke after this test:", key.split(".")[0], "member:", member)
    store.save(Entry("Fictional violet exam", date(2040, 3, 12)))
    turn = hosted.Turn(hosted.turn_id(), date.today())

    def bounded(bearer, body, cancel):
        if turn.step >= 2:
            raise RuntimeError("Fictional trial stopped at its two-request cap.")
        return turn.respond(bearer, body, cancel)

    text, plan, status = api.run(
        CalendarTools(store),
        key,
        config.DEFAULTS,
        [],
        "Find the fictional violet exam by title. Reply with its ISO date.",
        api.Cancellation(),
        bounded,
    )
    assert turn.step == 2 and "2040-03-12" in text and plan is None
    print("Hosted lookup passed.", status)
    command = """
Add-Type -AssemblyName System.Speech
$v=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $fmt=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
  $v.SetOutputToWaveFile([IO.Path]::Combine((Get-Location).Path,'trial.wav'),$fmt)
  $v.Speak('The fictional violet exam is on Tuesday.')
} finally {$v.Dispose()}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=folder,
        check=True,
        capture_output=True,
        timeout=25,
        env={k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")},
    )
    result = hosted.transcribe(key, (Path(folder) / "trial.wav").read_bytes(), api.Cancellation())
    assert "tuesday" in result.lower()
    print("Hosted synthetic transcription passed. Posted billing is separate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock-local")
    modes.add_argument(
        "--live-private-project",
        action="store_true",
        help="Owner confirmed sharing is disabled. May incur charges.",
    )
    args = parser.parse_args()
    invite = (
        sys.stdin.readline().strip()
        if args.mock_local
        else getpass("Private single-use fictional trial invitation: ")
    )
    with (
        TemporaryDirectory(prefix="calendar-hosted-trial-") as folder,
        patch.dict(os.environ, {"LOCALAPPDATA": folder}),
    ):
        with closing(Store(Path(folder) / "fictional.sqlite3")) as store:
            if args.mock_local:
                with patch.object(api, "post", local_only(args.mock_local)):
                    local_desktop(store, invite)
            else:
                live(store, invite, folder)
