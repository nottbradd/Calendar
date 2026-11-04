"""Bounded push-to-talk using the Windows waveform device, on the caller's worker thread."""

import ctypes
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4
import wave

MAX_SECONDS = 45


def mci(command):
    dll = ctypes.WinDLL("winmm")
    result = ctypes.create_unicode_buffer(256)
    code = dll.mciSendStringW(command, result, len(result), None)
    if code:
        raise ValueError(
            "Microphone unavailable. Check the input device and Windows microphone permission."
        )
    return result.value


class Recorder:
    def __init__(self, command=None):
        self.command = command or mci

    def capture(self, stop_event, cancel_event):
        alias = "cal" + uuid4().hex
        if cancel_event.is_set():
            raise InterruptedError()
        with TemporaryDirectory(prefix="Calendar-voice-") as directory:
            output = Path(directory) / "speech.wav"
            self.command(f"open new type waveaudio alias {alias}")
            try:
                self.command(
                    f"set {alias} time format milliseconds format tag pcm bitspersample 16 channels 1 samplespersec 16000 bytespersec 32000 alignment 2"
                )
                self.command(f"record {alias} to {MAX_SECONDS * 1000}")
                deadline = monotonic() + MAX_SECONDS
                while not stop_event.is_set() and monotonic() < deadline:
                    if cancel_event.wait(0.04):
                        raise InterruptedError()
                if cancel_event.is_set():
                    raise InterruptedError()
                self.command(f"stop {alias}")
                self.command(f'save {alias} "{output}"')
            finally:
                self.command(f"close {alias}")
            if cancel_event.is_set():
                raise InterruptedError()
            try:
                if output.stat().st_size > 2_000_000:
                    raise ValueError("Recording is too long.")
                data = output.read_bytes()
                with wave.open(io.BytesIO(data)) as wav:
                    frames = wav.getnframes()
                    if (
                        not frames
                        or frames > wav.getframerate() * MAX_SECONDS
                        or not any(wav.readframes(frames))
                    ):
                        raise ValueError("No usable audio was recorded.")
                return data
            except (OSError, wave.Error, EOFError) as exc:
                raise ValueError("No usable audio was recorded.") from exc
