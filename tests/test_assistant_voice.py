"""Exercise the recorder with synthetic PCM. The tests never open a microphone."""

import io
import math
import re
import struct
import wave
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from school_calendar import assistant_api
from school_calendar.assistant_voice import Recorder


class WaveDevice:
    """A fake Windows recording device which can finish, cancel, or reject capture."""

    def __init__(self, signal, *, frames=16000, reject=None):
        self.signal = signal
        self.frames = frames
        self.reject = reject
        self.saved = None
        self.closed = False

    def command(self, text):
        verb = text.partition(" ")[0].lower()
        if verb == self.reject:
            raise ValueError("The recording device could not be accessed.")
        if verb == "record":
            self.signal.set()
        elif verb == "save":
            self.saved = Path(re.search(r'"([^"]+)"', text)[1])
            with wave.open(str(self.saved), "wb") as stream:
                stream.setparams((1, 2, 16000, self.frames, "NONE", "not compressed"))
                samples = (int(1024 * math.sin(2 * math.pi * i / 40)) for i in range(self.frames))
                stream.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
        elif verb == "close":
            self.closed = True
        return ""


class AssistantVoiceTests(unittest.TestCase):
    def test_stop_returns_valid_wav_and_cleans_up_file(self):
        stop = Event()
        device = WaveDevice(stop)
        audio = Recorder(command=device.command).capture(stop, Event())
        with wave.open(io.BytesIO(audio), "rb") as clip:
            self.assertEqual((clip.getnchannels(), clip.getsampwidth(), clip.getframerate()), (1, 2, 16000))
            self.assertEqual(clip.getnframes(), 16000)
        self.assertTrue(device.closed)
        self.assertIsNotNone(device.saved)
        self.assertFalse(device.saved.exists())

    def test_cancel_releases_device_without_saving_audio(self):
        cancel = Event()
        device = WaveDevice(cancel)
        with self.assertRaises(InterruptedError):
            Recorder(command=device.command).capture(Event(), cancel)
        self.assertTrue(device.closed)
        self.assertIsNone(device.saved)

    def test_empty_wav_is_rejected_and_cleaned_up(self):
        stop = Event()
        device = WaveDevice(stop, frames=0)
        with self.assertRaises(ValueError):
            Recorder(command=device.command).capture(stop, Event())
        self.assertTrue(device.closed)
        self.assertFalse(device.saved.exists())

    def test_overlong_device_output_is_not_sent_to_transcription(self):
        stop = Event()
        device = WaveDevice(stop, frames=16001)
        with patch("school_calendar.assistant_voice.MAX_SECONDS", 1):
            with self.assertRaises(ValueError):
                Recorder(command=device.command).capture(stop, Event())
        self.assertTrue(device.closed)
        self.assertFalse(device.saved.exists())

    def test_unavailable_device_and_failed_recording_are_reported(self):
        for stage in ("open", "record", "save"):
            with self.subTest(stage=stage):
                stop = Event()
                device = WaveDevice(stop, reject=stage)
                with self.assertRaises(ValueError):
                    Recorder(command=device.command).capture(stop, Event())
                if stage != "open":
                    self.assertTrue(device.closed)

    def test_transcription_uses_audio_endpoint_and_yields_correctable_text(self):
        stop = Event()
        audio = Recorder(command=WaveDevice(stop).command).capture(stop, Event())
        cancel = assistant_api.Cancellation()
        with patch.object(
            assistant_api, "post", return_value={"text": "  Fictional fieldwork report.  "}
        ) as post:
            transcript = assistant_api.transcribe("sample-key", audio, cancel)
        key, endpoint, multipart, content_type, flag = post.call_args.args
        self.assertEqual(
            (key, endpoint, flag, transcript),
            ("sample-key", "audio/transcriptions", cancel, "Fictional fieldwork report."),
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b"gpt-transcribe", multipart)
        self.assertIn(audio, multipart)

    def test_empty_transcript_is_reported_and_blank_audio_never_leaves_device(self):
        cancel = assistant_api.Cancellation()
        with patch.object(assistant_api, "post", return_value={"text": " "}) as post:
            with self.assertRaises(assistant_api.ApiError):
                assistant_api.transcribe("sample-key", b"", cancel)
            post.assert_not_called()
            stop = Event()
            audio = Recorder(command=WaveDevice(stop).command).capture(stop, Event())
            with self.assertRaises(assistant_api.ApiError):
                assistant_api.transcribe("sample-key", audio, cancel)
