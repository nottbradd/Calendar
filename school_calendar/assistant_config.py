"""Per-Windows-user assistant preferences, outside the calendar and its journal."""

import json
import os
from pathlib import Path
import re
import tempfile
import winreg
from .sync_crypto import dpapi

DEFAULTS = {"mode": "Auto", "routine": "gpt-6-luna", "planning": "gpt-6.1-sol"}
MODES = ("Auto", "Routine", "Planning", "GPT-5.4")
PRIVACY = (
    "When you send a message, OpenAI receives your message, recent conversation and relevant calendar "
    "details. Stopping a recording sends the audio for transcription. Chat stays in this session. "
    "Responses use store:false. Default abuse monitoring can retain content for up to 30 days, subject "
    "to your organization’s controls and legal requirements."
)
SHARING = (
    "Your organization controls project data sharing. Opting in allows shared inputs and outputs to be "
    "used to improve OpenAI models. Eligible projects may receive complimentary tokens for eligible "
    "shared traffic within daily limits. Other traffic, tool rounds and transcription may incur charges. "
    "Check your project’s offer, usage and credit grants for actual billing."
)


def directory():
    return Path(os.environ["LOCALAPPDATA"]) / "CalenderAssistant"


def _write(name, data):
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, folder / name)
    finally:
        tmp.unlink(missing_ok=True)


def options():
    try:
        data = json.loads((directory() / "settings.json").read_text("utf-8"))
        return validate_options(data)
    except (ValueError, TypeError, OSError, KeyError):
        return DEFAULTS.copy()


def validate_options(data):
    if data["mode"] not in MODES or any(
        not re.fullmatch(r"[a-zA-Z0-9._:-]{1,120}", data[k]) for k in ("routine", "planning")
    ):
        raise ValueError("Choose a mode and valid model IDs.")
    return {k: data[k] for k in DEFAULTS}


def save_options(data):
    _write("settings.json", json.dumps(validate_options(data)).encode())


def save_key(key):
    if not key or len(key) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("Enter a valid project API key.")
    _write("project.key.dpapi", dpapi(key.encode()))


def remove_key():
    (directory() / "project.key.dpapi").unlink(missing_ok=True)


def project_key():
    p = directory() / "project.key.dpapi"
    if p.exists():
        return dpapi(p.read_bytes(), unlock=True).decode()
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"].strip()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
            return winreg.QueryValueEx(env, "OPENAI_API_KEY")[0].strip()
    except OSError:
        return ""
