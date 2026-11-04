"""Versioned Calendar cryptographic formats. All secret-key operations run on the PC."""
import base64
import ctypes
import hashlib
import hmac
import json
import re
import secrets
from ctypes import wintypes
from uuid import uuid4

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import Aead

from .sync_journal import packed


def encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode(text):
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def random_key():
    return encode(secrets.token_bytes(32))


def digest(text):
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def kit(prefix, calendar=None):
    return ".".join((prefix, calendar or uuid4().hex, random_key()))


def parse_kit(text, prefix):
    parts = text.strip().split(".")
    if (len(parts) != 3 or parts[0] != prefix or not re.fullmatch("[a-f0-9]{32}", parts[1])
            or not re.fullmatch("[A-Za-z0-9_-]{43}", parts[2]) or encode(decode(parts[2])) != parts[2]):
        raise ValueError("That code is incomplete or belongs to a different action.")
    return parts[1], parts[2]


# The v1 domain separators below are wire constants. Their original spelling is
# required to open existing calendars and pair with previously released clients.
def recovery(seed):
    def derive(label):
        return hmac.digest(decode(seed), b"Calender/recovery/v1/" + label, "sha256")
    return encode(derive(b"authentication")), PrivateKey(derive(b"encryption"))


def first_key(seed):
    return encode(hmac.digest(decode(seed), b"Calender/recovery/v1/initial-content-key", "sha256"))


def anchor(calendar, first):
    return digest("Calender/calendar/v1/" + calendar + "/" + first)


def member_tag(key, calendar, device, public):
    data = f"Calender/member/v1/{calendar}/{device}/{public}".encode("ascii")
    return hmac.digest(decode(key), data, "sha256").hex()


def identity(calendar):
    private = PrivateKey.generate()
    return {"calendar": calendar, "device": uuid4().hex, "token": random_key(),
            "private": encode(bytes(private)), "pub": encode(bytes(private.public_key))}


def fingerprint(device, public):
    return digest("Calender/verify/v1/" + device + "/" + public)[:12].upper()


def wrap(calendar, public, keys):
    message = packed({"calendar": calendar, "recipient": public, "keys": keys}).encode()
    return encode(SealedBox(PublicKey(decode(public))).encrypt(message))


def unwrap(calendar, private, box, epoch):
    message = json.loads(SealedBox(private).decrypt(decode(box)))
    keys = message["keys"]
    if (message["calendar"] != calendar or message["recipient"] != encode(bytes(private.public_key))
            or set(keys) != {str(n) for n in range(1, epoch + 1)}
            or any(len(decode(v)) != Aead.KEY_SIZE for v in keys.values())):
        raise ValueError("The calendar key envelope could not be verified.")
    return keys


def associated(calendar, op, epoch):
    return f"Calender/op/v1/{calendar}/{op}/{epoch}".encode("ascii")


def encrypt(calendar, keys, epoch, op, content):
    cipher = Aead(decode(keys[str(epoch)]))
    return encode(bytes(cipher.encrypt(packed(content).encode(), associated(calendar, op, epoch))))


def decrypt(calendar, keys, change):
    op, epoch = change["op"], change["epoch"]
    cipher = Aead(decode(keys[str(epoch)]))
    return json.loads(cipher.decrypt(decode(change["box"]), associated(calendar, op, epoch)))


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def dpapi(raw, *, unlock=False):
    # Current-user DPAPI with CRYPTPROTECT_UI_FORBIDDEN. No machine-wide fallback.
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if unlock else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    target = Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("Windows could not unlock this device profile. Your local calendar is available.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)
