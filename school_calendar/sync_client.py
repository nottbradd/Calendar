"""Encrypted device protocol. Each network task owns a separate Store connection."""
import json
import hmac
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from nacl.public import PrivateKey

from . import sync_crypto as crypto
from . import sync_journal as journal


SERVICE = "https://calender-sync-dev.nottbradd.workers.dev"


class SyncError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__({
            "denied": "Access denied. This device may have been revoked.",
            "invalid_invite": "Invitation expired, was replaced, or has already been claimed.",
            "retry": "The device list changed. Refresh and try again.",
            "refresh_keys": "Keys changed. Your pending changes will retry with the current key.",
            "slow_down": "Too many attempts. Wait a minute before retrying.",
            "device_limit": "The prototype device limit was reached. Revoke an unused device.",
            "too_large": "A change exceeds the prototype upload limit and is still saved locally.",
            "connection": "Connection problem. Pending changes are saved locally.",
            "profile_changed": "This device profile changed. Reopen Sync settings.",
        }.get(reason, "Sync could not complete. Your local entries are safe."))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a device bearer to a redirected host.


class Api:
    def __init__(self, base=SERVICE):
        self.base = base
        self.opener = build_opener(NoRedirect())
        self.requests = 0
        self.sent_bytes = self.received_bytes = 0

    def call(self, identity, action, body=None):
        raw = journal.packed(body or {}).encode()
        request = Request(self.base + "/v1/" + identity["calendar"] + "/" + action, raw, {
            "Content-Type": "application/json", "User-Agent": "Calender-Sync/1",
            "Authorization": "Bearer " + identity["device"] + "." + identity["token"],
        }, method="POST")
        self.requests += 1
        self.sent_bytes += len(raw)
        try:
            with self.opener.open(request, timeout=12) as response:
                result = response.read(18 * 1024 * 1024 + 1)
                self.received_bytes += len(result)
                return json.loads(result)
        except HTTPError as exc:
            with exc:
                try:
                    code = json.loads(exc.read(2048)).get("error")
                except (ValueError, UnicodeError):
                    code = None
            raise SyncError(code if code in {"denied", "invalid_invite", "slow_down", "device_limit",
                                             "retry", "refresh_keys", "too_large"} else "connection") from None
        except (URLError, OSError, ValueError) as exc:
            raise SyncError("connection") from None


def sealed_profile(identity):
    return crypto.dpapi(journal.packed(identity).encode())


def summary(db):
    row = db.execute("SELECT phase,contact,device FROM sync_profile").fetchone()
    if not row:
        return {"phase": "disabled", "pending": 0, "oversized": 0, "conflicts": [], "contact": None,
                "retained": db.execute("SELECT count(*) FROM sync_ops WHERE pending>0").fetchone()[0]}
    return {**dict(row), "pending": db.execute("SELECT count(*) FROM sync_ops WHERE pending>0").fetchone()[0],
            "oversized": db.execute("SELECT count(*) FROM sync_ops WHERE pending=2").fetchone()[0],
            "conflicts": journal.conflicts(db)}


class Sync:
    def __init__(self, store, api=None):
        self.store, self.db, self.api = store, store.db, api or Api()

    def profile(self):
        row = self.db.execute("SELECT phase,secret FROM sync_profile").fetchone()
        if not row:
            raise SyncError("profile_changed")
        return row["phase"], json.loads(crypto.dpapi(row["secret"], unlock=True))

    def begin(self, mode, code, reset=False):
        prefix = "CALI1" if mode == "join" else "CALR1"
        if mode == "join":
            code, separator, anchor = code.strip().rpartition(".")
            if not separator or len(anchor) != 64 or any(c not in "0123456789abcdef" for c in anchor):
                raise ValueError("That code is incomplete or belongs to a different action.")
        calendar, seed = crypto.parse_kit(code, prefix)
        ident = crypto.identity(calendar)
        first = None if mode == "join" else crypto.first_key(seed)
        ident["anchor"] = anchor if mode == "join" else crypto.anchor(calendar, first)
        if mode == "join":
            ident["setup"] = {"pub": ident["pub"], "ticket": seed}
        elif mode == "create":
            proof, recovery_key = crypto.recovery(seed)
            pub = crypto.encode(bytes(recovery_key.public_key))
            keys = {"1": first}
            ident["setup"] = {"pub": ident["pub"], "recoveryPub": pub, "recoveryHash": crypto.digest(proof),
                              "tag": crypto.member_tag(first, calendar, ident["device"], ident["pub"]),
                              "recoveryTag": crypto.member_tag(first, calendar, "recovery", pub),
                              "box": crypto.wrap(calendar, ident["pub"], keys), "recoveryBox": crypto.wrap(calendar, pub, keys)}
        elif mode == "recover":
            ident["setup"] = {"seed": seed, "reset": reset}
        else:
            raise ValueError("Unknown pairing action.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM sync_profile").fetchone():
                raise ValueError("Disconnect the current profile before linking another calendar.")
            self.db.execute("INSERT INTO sync_profile(slot,device,phase,secret) VALUES (1,?,?,?)",
                            (ident["device"], mode, sealed_profile(ident)))
            if journal.selected_scope(self.db) is None:
                journal.bind_scope(self.db, ident["anchor"], False)
                journal.seed(self.store)
            # A previous calendar remains open and editable until enrollment succeeds.
        return ident["device"]

    def _check(self, ident):
        if not self.db.execute("SELECT 1 FROM sync_profile WHERE device=?", (ident["device"],)).fetchone():
            raise SyncError("profile_changed")

    def _state(self, ident, state):
        keys = crypto.unwrap(ident["calendar"], PrivateKey(crypto.decode(ident["private"])), state["box"], state["epoch"])
        latest = keys[str(state["epoch"])]
        members = [*state["members"], {"id": "recovery", "pub": state["recoveryPub"], "tag": state["recoveryTag"]}]
        if (not hmac.compare_digest(ident["anchor"], crypto.anchor(ident["calendar"], keys["1"]))
                or any(keys.get(k) != v for k, v in ident.get("keys", {}).items())
                or not any(m["id"] == ident["device"] and m["pub"] == ident["pub"] for m in state["members"])
                or any(not hmac.compare_digest(m["tag"], crypto.member_tag(latest, ident["calendar"], m["id"], m["pub"]))
                       for m in members)):
            raise ValueError("The calendar key envelope or device list could not be verified.")
        ident.pop("setup", None)
        ident.update(keys=keys, state={k: v for k, v in state.items() if k != "box"})
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._check(ident)
            journal.activate_scope(self.store, ident["anchor"])
            self.db.execute("UPDATE sync_profile SET secret=?,phase='active',contact=? WHERE device=?",
                            (sealed_profile(ident), datetime.now(timezone.utc).isoformat(timespec="seconds"), ident["device"]))
        return state

    def connect(self):
        phase, ident = self.profile()
        if phase == "active":
            return self._state(ident, self.api.call(ident, "state"))
        setup = ident["setup"]
        if phase == "recover":
            proof, private = crypto.recovery(setup["seed"])
            info = self.api.call(ident, "recover", {"proof": proof})
            if "members" in info:  # Previous registration succeeded before the reply was received.
                return self._state(ident, info)
            keys = crypto.unwrap(ident["calendar"], private, info["box"], info["epoch"])
            if not hmac.compare_digest(ident["anchor"], crypto.anchor(ident["calendar"], keys["1"])):
                raise ValueError("The calendar key envelope could not be verified.")
            epoch = info["epoch"]
            extra = {}
            if setup.get("reset"):
                epoch += 1
                keys[str(epoch)] = crypto.random_key()
                pub = crypto.encode(bytes(private.public_key))
                extra = {"reset": True, "recoveryBox": crypto.wrap(ident["calendar"], pub, keys),
                         "recoveryTag": crypto.member_tag(keys[str(epoch)], ident["calendar"], "recovery", pub)}
            setup = {"proof": proof, "pub": ident["pub"], "generation": info["generation"],
                     **extra, "tag": crypto.member_tag(keys[str(epoch)], ident["calendar"], ident["device"], ident["pub"]),
                     "box": crypto.wrap(ident["calendar"], ident["pub"], keys)}
        action = {"create": "create", "join": "claim", "recover": "recover"}[phase]
        result = self.api.call(ident, action, setup)
        if result.get("waiting"):
            with self.db:
                self.db.execute("UPDATE sync_profile SET contact=? WHERE device=?",
                                (datetime.now(timezone.utc).isoformat(timespec="seconds"), ident["device"]))
        else:
            self._state(ident, result)
        return result

    def invite(self, seconds=600):
        self.connect()
        _, ident = self.profile()
        ticket = crypto.kit("CALI1", ident["calendar"])
        _, seed = crypto.parse_kit(ticket, "CALI1")
        state = self.api.call(ident, "invite", {"hash": crypto.digest(seed), "seconds": seconds,
                                                "generation": ident["state"]["generation"]})
        self._state(ident, state)
        return ticket + "." + ident["anchor"]

    def approve(self, code):
        self.connect()
        _, ident = self.profile()
        state = ident["state"]
        pending = state["pending"]
        if not pending or code.strip().replace("-", "").upper() != crypto.fingerprint(pending["id"], pending["pub"]):
            raise ValueError("The verification code does not match the requesting PC.")
        result = self.api.call(ident, "approve", {"generation": state["generation"], "device": pending["id"],
                               "tag": crypto.member_tag(ident["keys"][str(state["epoch"])], ident["calendar"], pending["id"], pending["pub"]),
                               "pub": pending["pub"], "box": crypto.wrap(ident["calendar"], pending["pub"], ident["keys"])})
        self._state(ident, result)

    def revoke(self, device):
        self.connect()
        _, ident = self.profile()
        state = ident["state"]
        keys = dict(ident["keys"])
        latest = crypto.random_key()
        keys[str(state["epoch"] + 1)] = latest
        boxes = {m["id"]: crypto.wrap(ident["calendar"], m["pub"], keys) for m in state["members"] if m["id"] != device}
        tags = {m["id"]: crypto.member_tag(latest, ident["calendar"], m["id"], m["pub"]) for m in state["members"] if m["id"] != device}
        result = self.api.call(ident, "revoke", {"generation": state["generation"], "device": device, "boxes": boxes,
                               "tags": tags, "recoveryTag": crypto.member_tag(latest, ident["calendar"], "recovery", state["recoveryPub"]),
                               "recoveryBox": crypto.wrap(ident["calendar"], state["recoveryPub"], keys)})
        self._state(ident, result)

    def disconnect(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if journal.conflicts(self.db):
                raise ValueError("Resolve competing versions before disconnecting so each choice is retained.")
            if journal.selected_scope(self.db) is None and self.db.execute("SELECT 1 FROM sync_profile").fetchone():
                # Retain the binding for an upgraded 1.3.0 installation even if it has
                # stayed offline since the upgrade. Failure to unlock rolls back disconnect.
                phase, ident = self.profile()
                journal.bind_scope(self.db, ident["anchor"], phase == "active")
            self.db.execute("DELETE FROM sync_profile")

    def _pull(self, ident):
        while True:
            row = self.db.execute("SELECT cursor FROM sync_profile WHERE device=?", (ident["device"],)).fetchone()
            if not row:
                raise SyncError("profile_changed")
            cursor = row[0]
            page = self.api.call(ident, "pull", {"after": cursor})
            if ident["state"]["generation"] != page["generation"]:
                self._state(ident, self.api.call(ident, "state"))
            items = [(change, crypto.decrypt(ident["calendar"], ident["keys"], change)) for change in page["changes"]]
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self._check(ident)
                for change, value in items:
                    journal.receive(self.db, change["op"], value)
                    cursor = max(cursor, change["seq"])
                self.db.execute("UPDATE sync_profile SET cursor=max(cursor,?),contact=? WHERE device=?",
                                (cursor, datetime.now(timezone.utc).isoformat(timespec="seconds"), ident["device"]))
            if not page["more"]:
                return

    def run(self):
        phase, ident = self.profile()
        if phase != "active":
            if self.connect().get("waiting"):
                return {"waiting": True, "uploaded": 0}
            _, ident = self.profile()
        self._pull(ident)
        uploaded = 0
        while True:
            batch = []
            size = 0
            epoch = ident["state"]["epoch"]
            with self.db:
                # A different application window can disconnect or switch calendars
                # between pull and push. Read identity and outbox in one SQLite snapshot.
                self.db.execute("BEGIN")
                self._check(ident)
                rows = self.db.execute("SELECT * FROM sync_ops WHERE pending=1 ORDER BY rowid LIMIT 24").fetchall()
            for row in rows:
                content = {"entity": row["entity"], "parents": json.loads(row["parents"]), "value": json.loads(row["payload"])}
                box = crypto.encrypt(ident["calendar"], ident["keys"], epoch, row["op"], content)
                if len(box) > 700000:
                    raise SyncError("too_large")
                if size + len(box) > 960000:
                    break
                batch.append({"op": row["op"], "box": box})
                size += len(box)
            if not batch:
                break
            receipt = self.api.call(ident, "push", {"epoch": epoch, "changes": batch})
            ids = [b["op"] for b in batch]
            if receipt.get("accepted") != ids:
                raise SyncError("connection")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self._check(ident)
                self.db.executemany("UPDATE sync_ops SET pending=0 WHERE op=?", ((op,) for op in ids))
            uploaded += len(ids)
        if uploaded:
            self._pull(ident)
        return {"uploaded": uploaded}
