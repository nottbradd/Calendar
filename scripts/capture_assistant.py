"""Capture the real assistant UI with an isolated calendar and a fictional proposal."""

import argparse
import io
import json
import os
import sys
from contextlib import closing
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def show_proposal(panel, store):
    from PySide6.QtTest import QTest
    from school_calendar.assistant_api import CHANGES, FILTERS

    sample = next(entry for entry in store.read().entries if entry.subject == "English")
    calls = [
        ("find_entries", {**dict.fromkeys(FILTERS), "title": sample.title}),
        (
            "propose_changes",
            {
                "actions": [
                    {
                        "op": "edit",
                        "id": sample.id,
                        "changes": {**dict.fromkeys(CHANGES), "at": "10:30", "end_at": "11:30"},
                    }
                ],
                "fixed_ids": [],
            },
        ),
    ]

    def reply(key, body, cancel):
        name, arguments = calls.pop(0)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": f"sample-{name}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
            ],
        }

    with (
        patch("school_calendar.assistant_api.response", side_effect=reply),
        patch(
            "school_calendar.assistant_api.post", side_effect=AssertionError("Unexpected network access")
        ),
    ):
        panel.composer.setText("Move my English practice SAC back by 30 minutes.")
        panel.send_button.click()
        until = monotonic() + 8
        while not panel.apply_button.isVisible() and monotonic() < until:
            QTest.qWait(25)
        if calls or not panel.apply_button.isVisible():
            raise RuntimeError("The fictional proposal did not reach the review card")
        if next(e for e in store.read().entries if e.id == sample.id) != sample:
            raise RuntimeError("A preview changed the calendar before Apply")


def show_transcript(panel):
    """Demonstrate the editable draft using generated audio and a simulated transcript."""
    from PySide6.QtTest import QTest

    text = "What assessments do I have next week?"
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 48000, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x01\x00\xff" * 24000)

    def capture(stop, cancel):
        if not stop.wait(3):
            raise RuntimeError("The sample recording did not stop")
        return audio.getvalue()

    with (
        patch("school_calendar.assistant_ui.Recorder") as recorder,
        patch("school_calendar.assistant_api.transcribe", return_value=text),
        patch(
            "school_calendar.assistant_api.post", side_effect=AssertionError("Unexpected network access")
        ),
    ):
        recorder.return_value.capture.side_effect = capture
        panel.mic_button.click()
        panel.stop_button.click()
        deadline = monotonic() + 5
        while panel.composer.text() != text and monotonic() < deadline:
            QTest.qWait(25)
        if panel.composer.text() != text or panel.composer.isReadOnly():
            raise RuntimeError("The transcript is missing from the editable composer")


def capture(folder):
    from PySide6.QtCore import QLocale
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from school_calendar.appearance import apply_theme
    from school_calendar.assistant_config import save_key
    from school_calendar.model import Entry
    from school_calendar.storage import Store
    from school_calendar.window import CalendarWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QLocale.setDefault(QLocale("en_AU"))
    apply_theme(app)
    today = date(2026, 11, 4)
    with (
        TemporaryDirectory(prefix="calendar-assistant-picture-") as temporary,
        patch.dict(os.environ, {"LOCALAPPDATA": temporary, "OPENAI_API_KEY": ""}),
    ):
        save_key("sample-only-screenshot-key")
        with closing(Store(Path(temporary) / "calendar.sqlite3")) as store:
            for title, subject, kind, day, start, end in (
                ("Fieldwork report", "Geography", "Assignment", 5, None, None),
                ("English practice SAC", "English", "SAC", 6, time(10), time(11)),
                ("Studio critique", "Art", "SAC", 9, time(14), time(15)),
                ("Data analysis", "Maths", "Exam", 12, time(9), time(10)),
            ):
                store.save(Entry(title, date(2026, 11, day), subject, kind, start, end_at=end))
            window = CalendarWindow(store, lambda: today)
            window.resize(1230, 760)
            window.show()
            try:
                window.open_assistant()
                panel = window.assistant_panel
                show_proposal(panel, store)
                folder.mkdir(parents=True, exist_ok=True)
                for filename, width, height in (
                    ("assistant-review.png", 1230, 760),
                    ("assistant-compact.png", 800, 550),
                ):
                    window.resize(width, height)
                    if filename == "assistant-compact.png":
                        panel.apply_button.click()
                        sample = next(e for e in store.read().entries if e.subject == "English")
                        if (sample.at, sample.end_at) != (time(10, 30), time(11, 30)):
                            raise RuntimeError("The reviewed change did not reach the sample database")
                        show_transcript(panel)
                    QTest.qWait(300)
                    for button in (panel.send_button, panel.mic_button):
                        if not button.isVisible() or button.visibleRegion().isEmpty():
                            raise RuntimeError(f"{button.text()} is hidden at {width} × {height}")
                    if not window.grab().save(str(folder / filename)):
                        raise RuntimeError(f"Could not save {filename}")
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Destination for the two screenshots")
    options = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    capture(options.folder)
