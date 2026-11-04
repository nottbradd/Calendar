"""Invite-only Calendar AI transport. Its device bearer is independent of sync and personal API keys."""

from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from uuid import uuid4
import wave

from . import assistant_api as api, assistant_config as config
from .sync_crypto import dpapi

HOST = "calendar-ai-beta.nottbradd.workers.dev"
CONSENT = (
    "Hosted use sends messages, recent chat, selected calendar details and submitted audio "
    "through the Calendar AI service to OpenAI. Changes are reviewed and saved on this PC. "
    "Encrypted calendar sync is separate. "
    "The beta requires a project with input/output sharing disabled."
)
MESSAGES = {
    "revoked": "Hosted access is inactive or revoked. Contact the beta owner.",
    "seats_full": "The hosted beta is full. Contact the beta owner.",
    "invalid_invite": "This invitation is invalid, expired, or already used. Ask the owner for a new invitation.",
    "daily_allowance": "Your devices have used today's hosted chat turns. They reset at 00:00 UTC.",
    "planning_allowance": "Today's hosted planning allowance is used. Routine requests can still use remaining turns. Planning resets at 00:00 UTC.",
    "audio_allowance": "This clip exceeds your remaining daily hosted audio. Try a shorter recording or type your message. Audio resets at 00:00 UTC.",
    "shared_budget": "The beta's shared spending ceiling was reached. Contact the beta owner.",
    "service_paused": "The owner has paused the hosted assistant.",
    "already_submitted": "The service already received this request. Clear chat to deliberately start a new turn.",
    "invalid_round": "This hosted turn expired or cannot continue. Clear chat to start a new turn.",
    "too_large": "This request exceeds the hosted limit. Shorten the message, search or recording.",
    "invalid_request": "The hosted service could not accept this request. Try a narrower calendar question.",
}


def error_message(status, error):
    return (
        MESSAGES.get(error, "Hosted AI is temporarily unavailable. Your draft is still here.")
        if isinstance(error, str)
        else "Hosted AI is temporarily unavailable."
    )


def provider():
    path = config.directory() / "provider.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return "Personal"
    except (OSError, ValueError):
        return "Setup required"
    if data == {"provider": "Hosted", "consent": 1}:
        return "Hosted"
    return "Personal" if data == {"provider": "Personal"} else "Setup required"


def select(name, consent=False):
    if name == "Hosted" and (not consent or not active()):
        raise ValueError("Accept the hosted privacy notice and activate this PC first.")
    if name not in ("Hosted", "Personal"):
        raise ValueError("Choose an assistant provider.")
    config._write(
        "provider.json",
        json.dumps({"provider": name, **({"consent": 1} if name == "Hosted" else {})}).encode(),
    )


def _path():
    return config.directory() / "hosted-device.dpapi"


def _device():
    return json.loads(dpapi(_path().read_bytes(), unlock=True))


def active():
    try:
        return bool(_device().get("member"))
    except (OSError, ValueError):
        return False


def credential():
    if provider() != "Hosted" or not active():
        raise api.ApiError("Activate hosted access and accept the notice in Assistant settings.")
    device = _device()
    return device["id"] + "." + device["token"]


def forget():
    _path().unlink(missing_ok=True)


def activate(invite, accepted, cancel):
    if not accepted:
        raise ValueError("Accept the hosted privacy notice first.")
    value = invite.strip()
    if not re.fullmatch(r"CAI1\.[A-Za-z0-9_-]{43}", value):
        raise ValueError("Enter a complete Calendar AI invitation.")
    path = _path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"id": uuid4().hex, "token": secrets.token_urlsafe(32)}
        # Publish a complete key file exclusively, before enrollment. A lost
        # response or a concurrent settings window must reuse this credential.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(dpapi(json.dumps(data).encode()))
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    data = _device()
    result = api.post(
        data["id"] + "." + data["token"],
        "enroll",
        json.dumps({"invite": value[5:]}).encode(),
        "application/json",
        cancel,
        host=HOST,
        error_hint=error_message,
    )
    if result.get("device") != data["id"] or not re.fullmatch(
        r"[a-f0-9]{32}", str(result.get("member", ""))
    ):
        raise api.ApiError("Could not verify hosted activation.")
    config._write(path.name, dpapi(json.dumps({**data, "member": result["member"]}).encode()))
    return result["member"]


def turn_id():
    return f"{int(time.time())}-{uuid4().hex}"


class Turn:
    def __init__(self, identifier, today):
        self.identifier = identifier
        self.today = today.isoformat()
        self.zone = datetime.now().astimezone().strftime("%z")
        self.step = 0

    def respond(self, bearer, body, cancel):
        data = {
            "turn": self.identifier,
            "step": self.step,
            "date": self.today,
            "zone": self.zone,
            "input": body["input"],
        }
        res = api.post(
            bearer,
            "chat",
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
            "application/json",
            cancel,
            host=HOST,
            error_hint=error_message,
        )
        self.step += 1
        return res


def transcribe(bearer, audio, cancel):
    with wave.open(io.BytesIO(audio)) as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (
            1,
            2,
            16000,
        ) or not 0 < stream.getnframes() <= 45 * 16000:
            raise api.ApiError("Use a mono recording of up to 45 seconds.")
        pcm = stream.readframes(stream.getnframes())
    result = api.post(
        bearer,
        "transcribe",
        pcm,
        "application/octet-stream",
        cancel,
        host=HOST,
        error_hint=error_message,
        extra_headers={"X-Calendar-Request": turn_id()},
    )
    text = result.get("text")
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 2400:
        raise api.ApiError("No usable transcript was returned. Your draft is still here.")
    return text.strip()
